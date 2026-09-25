"""Tests for ServiceAuthBootstrap 3-tier credentials vault (L1 Memory, L2 Redis, L3 PostgreSQL)."""

from typing import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.crypto import decrypt_token, encrypt_token, set_fernet
from core.database.base import Base
from core.database.models import SystemState
from core.intraservice.auth import (
    ServiceAuthBootstrap,
    ServiceAuthCredentials,
    ServiceAuthError,
    set_service_auth_session_factory,
)
from core.intraservice.client import IntraServiceClient


class MockRedis:
    def __init__(self) -> None:
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys: str):
        for k in keys:
            self.store.pop(k, None)

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.store)


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture(autouse=True)
def setup_fernet():
    key = Fernet.generate_key().decode()
    cipher = Fernet(key.encode())
    set_fernet(cipher)
    yield
    set_fernet(None)


@pytest.fixture
async def test_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    set_service_auth_session_factory(factory)
    yield factory
    set_service_auth_session_factory(None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_service_auth_l1_memory_hit(mock_redis):
    """Tier 1: In-memory cache returns instantly without touching Redis or DB."""
    bootstrap = ServiceAuthBootstrap()
    creds = ServiceAuthCredentials(auth_b64="dGVzdDoxMjM=", bot_user_id=10, login="bot_user")
    bootstrap._cached_credentials = creds

    res = await bootstrap.bootstrap_auth(redis_client=mock_redis)
    assert res.auth_b64 == "dGVzdDoxMjM="
    assert res.bot_user_id == 10
    assert res.login == "bot_user"
    assert await mock_redis.get(ServiceAuthBootstrap.KEY_SERVICE_AUTH_B64) is None


@pytest.mark.asyncio
async def test_service_auth_l2_redis_hit(mock_redis):
    """Tier 2: Redis Vault hit decrypts token and warms up L1 memory cache."""
    bootstrap = ServiceAuthBootstrap()
    plain_token = "Ym90OnNlY3JldA=="
    enc_token = encrypt_token(plain_token)

    await mock_redis.set(ServiceAuthBootstrap.KEY_SERVICE_AUTH_B64, enc_token)
    await mock_redis.set(ServiceAuthBootstrap.KEY_SERVICE_USER_ID, "42")
    await mock_redis.set(ServiceAuthBootstrap.KEY_SERVICE_LOGIN, "autopilot_bot")

    res = await bootstrap.bootstrap_auth(redis_client=mock_redis)
    assert res.auth_b64 == plain_token
    assert res.bot_user_id == 42
    assert res.login == "autopilot_bot"
    assert bootstrap._cached_credentials is not None
    assert bootstrap._cached_credentials.bot_user_id == 42


@pytest.mark.asyncio
async def test_service_auth_l3_postgres_vault_cold_start(mock_redis, test_session_factory):
    """Tier 3: Cold Start with empty Redis reads from PostgreSQL SystemState and auto-warms Redis."""
    plain_token = "cG9zdGdyZXM6Ym90"
    enc_token = encrypt_token(plain_token)

    # Insert into PostgreSQL SystemState durable vault
    async with test_session_factory() as session:
        row = SystemState(
            key=ServiceAuthBootstrap.STATE_KEY_CREDENTIALS,
            state_data={
                "encrypted_token": enc_token,
                "bot_user_id": 99,
                "login": "db_bot",
            },
        )
        session.add(row)
        await session.commit()

    # Redis is completely empty (simulating FLUSHALL / Cold Start)
    assert await mock_redis.get(ServiceAuthBootstrap.KEY_SERVICE_AUTH_B64) is None

    bootstrap = ServiceAuthBootstrap(session_factory=test_session_factory)
    res = await bootstrap.bootstrap_auth(redis_client=mock_redis, session_factory=test_session_factory)

    assert res.auth_b64 == plain_token
    assert res.bot_user_id == 99
    assert res.login == "db_bot"

    # Verify L2 (Redis) was automatically warmed up
    cached_redis_token = await mock_redis.get(ServiceAuthBootstrap.KEY_SERVICE_AUTH_B64)
    assert cached_redis_token == enc_token
    assert await mock_redis.get(ServiceAuthBootstrap.KEY_SERVICE_USER_ID) == "99"
    assert await mock_redis.get(ServiceAuthBootstrap.KEY_SERVICE_LOGIN) == "db_bot"


@pytest.mark.asyncio
async def test_service_auth_save_credentials_all_tiers(mock_redis, test_session_factory):
    """Verify save_credentials persists atomically to L1, L2 (Redis) and L3 (PostgreSQL)."""
    bootstrap = ServiceAuthBootstrap(session_factory=test_session_factory)

    saved = await bootstrap.save_credentials(
        auth_b64="YWRtaW46cGFzcw==",
        bot_user_id=105,
        login="admin_bot",
        redis_client=mock_redis,
        session_factory=test_session_factory,
    )
    assert saved.bot_user_id == 105
    assert saved.login == "admin_bot"

    # L1 verified
    assert bootstrap._cached_credentials.login == "admin_bot"

    # L2 verified
    assert await mock_redis.get(ServiceAuthBootstrap.KEY_SERVICE_USER_ID) == "105"
    assert await mock_redis.get(ServiceAuthBootstrap.KEY_SERVICE_LOGIN) == "admin_bot"

    # L3 verified
    async with test_session_factory() as session:
        from sqlalchemy import select
        res = await session.execute(
            select(SystemState).where(SystemState.key == ServiceAuthBootstrap.STATE_KEY_CREDENTIALS)
        )
        row = res.scalar_one_or_none()
        assert row is not None
        assert row.state_data["bot_user_id"] == 105
        assert row.state_data["login"] == "admin_bot"
        assert decrypt_token(row.state_data["encrypted_token"]) == "YWRtaW46cGFzcw=="


@pytest.mark.asyncio
async def test_service_auth_missing_credentials_raises_error(mock_redis, test_session_factory, monkeypatch):
    """If credentials missing across L1, L2, L3 and environment, ServiceAuthError is raised."""
    monkeypatch.delenv("INTRASERVICE_BOT_LOGIN", raising=False)
    monkeypatch.delenv("INTRASERVICE_SERVICE_LOGIN", raising=False)
    monkeypatch.delenv("INTRASERVICE_LOGIN", raising=False)
    monkeypatch.delenv("INTRASERVICE_BOT_PASSWORD", raising=False)
    monkeypatch.delenv("INTRASERVICE_SERVICE_PASSWORD", raising=False)
    monkeypatch.delenv("INTRASERVICE_PASSWORD", raising=False)

    bootstrap = ServiceAuthBootstrap(session_factory=test_session_factory)
    with pytest.raises(ServiceAuthError, match="Service bot credentials missing"):
        await bootstrap.bootstrap_auth(redis_client=mock_redis, session_factory=test_session_factory)
