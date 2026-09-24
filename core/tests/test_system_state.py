"""Unit and integration tests for SystemState model, dual watermark service and crypto utilities."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.crypto import (
    decrypt_secret,
    decrypt_token,
    encrypt_secret,
    encrypt_token,
    set_fernet,
)
from core.database.base import Base
from core.database.models import SystemState
from core.database.system_state import (
    WatermarkDTO,
    WatermarkService,
    set_system_state_session_factory,
)


@pytest.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    set_system_state_session_factory(session_factory)
    yield session_factory

    set_system_state_session_factory(None)
    await engine.dispose()


def test_system_state_model_instantiation():
    """Verify SystemState model fields and default state_data dict."""
    now = datetime.now(timezone.utc)
    state = SystemState(
        key="test_cursor",
        last_poll_at=now,
        last_task_id=1055,
        state_data={"consecutive_trips": 0, "active": True},
    )
    assert state.key == "test_cursor"
    assert state.last_poll_at == now
    assert state.last_task_id == 1055
    assert state.state_data == {"consecutive_trips": 0, "active": True}


def test_crypto_fernet_roundtrip():
    """Verify encryption and decryption with Fernet symmetric key."""
    test_key = Fernet.generate_key().decode()
    cipher = Fernet(test_key.encode())
    set_fernet(cipher)

    try:
        plain_token = "Ym90X3VzZXI6c2VjcmV0cGFzc3dvcmQ="
        encrypted = encrypt_token(plain_token)
        assert encrypted != plain_token

        decrypted = decrypt_token(encrypted)
        assert decrypted == plain_token

        # Test secret encryption
        secret_enc = encrypt_secret("my_super_secret")
        assert decrypt_secret(secret_enc) == "my_super_secret"

        # Test legacy unencrypted token fallback (InvalidToken)
        assert decrypt_token("plain_legacy_token") == "plain_legacy_token"
    finally:
        set_fernet(None)


def test_crypto_without_key_fallback():
    """Verify graceful plaintext fallback when ENCRYPTION_KEY is not set."""
    set_fernet(None)
    token = "some_raw_b64_token"
    assert encrypt_token(token) == token
    assert decrypt_token(token) == token

    # Empty inputs
    assert encrypt_token("") == ""
    assert decrypt_token("") == ""

    # Secret encryption fails closed without key
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY is required"):
        encrypt_secret("test")


@pytest.mark.asyncio
async def test_watermark_service_db_persistence(test_db):
    """Verify persisting and reading watermark cursor from PostgreSQL (SQLite in test)."""
    service = WatermarkService(session_factory=test_db)
    poll_time = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Read nonexistent watermark -> default DTO
    initial = await service.get_watermark(key="poller_1")
    assert initial.key == "poller_1"
    assert initial.last_poll_at is None
    assert initial.last_task_id is None

    # 2. Set watermark
    saved = await service.set_watermark(
        key="poller_1",
        last_poll_at=poll_time,
        last_task_id=2000,
        state_data={"mode": "active"},
    )
    assert saved.key == "poller_1"
    assert saved.last_poll_at == poll_time
    assert saved.last_task_id == 2000
    assert saved.state_data == {"mode": "active"}

    # 3. Read back from DB
    loaded = await service.get_watermark(key="poller_1")
    assert loaded.last_poll_at == poll_time
    assert loaded.last_task_id == 2000
    assert loaded.state_data == {"mode": "active"}

    # 4. Update existing watermark
    new_poll_time = datetime(2026, 9, 24, 12, 0, 30, tzinfo=timezone.utc)
    updated = await service.set_watermark(
        key="poller_1",
        last_poll_at=new_poll_time,
        last_task_id=2005,
    )
    assert updated.last_poll_at == new_poll_time
    assert updated.last_task_id == 2005
    # Previous state_data preserved
    assert updated.state_data == {"mode": "active"}


@pytest.mark.asyncio
async def test_dual_watermark_redis_fast_tier_and_warmup(test_db):
    """Verify Redis fast path and DB cache warm-up on cache miss."""
    mock_redis = AsyncMock()
    service = WatermarkService(session_factory=test_db, redis_client=mock_redis)

    # 1. Redis Cache Hit
    cached_dto = WatermarkDTO(
        key="ingestion_poller",
        last_poll_at=datetime(2026, 9, 24, 14, 0, 0, tzinfo=timezone.utc),
        last_task_id=500,
        state_data={"source": "redis_cache"},
    )
    mock_redis.get.return_value = cached_dto.model_dump_json()

    res = await service.get_watermark(key="ingestion_poller")
    assert res.last_task_id == 500
    assert res.state_data == {"source": "redis_cache"}
    mock_redis.get.assert_awaited_once_with("system_state:ingestion_poller")

    # 2. Redis Cache Miss -> DB read -> Warm up Redis
    mock_redis.get.return_value = None
    mock_redis.reset_mock()

    # Pre-populate DB
    poll_time = datetime(2026, 9, 24, 14, 10, 0, tzinfo=timezone.utc)
    await service.set_watermark(
        key="ingestion_poller",
        last_poll_at=poll_time,
        last_task_id=600,
        state_data={"source": "db"},
    )

    # Verify set_watermark wrote both to Redis system_state and compatibility keys
    mock_redis.set.assert_any_call("system_state:ingestion_poller", pytest.approx_json if hasattr(pytest, "approx_json") else mock_redis.set.call_args_list[0][0][1])
    mock_redis.set.assert_any_call("worker:last_check_time", poll_time.isoformat())
    mock_redis.set.assert_any_call("worker:service_last_task_id", "600")

    # Now read when Redis returns None (simulating cold start or eviction)
    mock_redis.get.return_value = None
    db_loaded = await service.get_watermark(key="ingestion_poller")
    assert db_loaded.last_task_id == 600

    # Verify warm up occurred
    mock_redis.set.assert_any_call(
        "system_state:ingestion_poller",
        db_loaded.model_dump_json(),
    )
