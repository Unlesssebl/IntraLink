"""Unit tests for AutopilotPolicyService, Redis caching, and Circuit Breaker."""

from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.autopilot.dto import AutopilotPolicyUpdateDTO
from core.autopilot.policy_service import (
    CIRCUIT_BREAKER_WINDOW_SEC,
    AutopilotPolicyService,
)
from core.database.base import Base
from core.database.models import AutopilotPolicy


class MockRedis:
    """In-memory mock for Redis."""

    def __init__(self) -> None:
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0):
        self.store[key] = value
        return True

    async def delete(self, key: str):
        self.store.pop(key, None)


@pytest.fixture
async def async_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture
def policy_service(async_session_factory, mock_redis) -> AutopilotPolicyService:
    return AutopilotPolicyService(
        session_factory=async_session_factory,
        redis_client=mock_redis,
    )


@pytest.mark.asyncio
async def test_get_policy_seeding_and_caching(policy_service, mock_redis):
    # 1. First fetch - seeds from baseline YAML and populates Redis
    policy = await policy_service.get_policy("install_printer")
    assert policy.scenario_key == "install_printer"
    assert policy.mode in ("ASSISTED", "FULL_AUTO")
    assert policy.consecutive_failures == 0
    assert not policy.is_circuit_broken

    # Verify Redis is warmed
    cached = await mock_redis.get("autopilot:policy:install_printer")
    assert cached is not None
    assert "install_printer" in cached

    # 2. Second fetch - served from Redis
    policy2 = await policy_service.get_policy("install_printer")
    assert policy2.scenario_key == "install_printer"
    assert policy2.mode == policy.mode


@pytest.mark.asyncio
async def test_update_policy(policy_service, mock_redis):
    # Update policy to FULL_AUTO
    update_req = AutopilotPolicyUpdateDTO(mode="FULL_AUTO", min_confidence=0.92)
    updated = await policy_service.update_policy("install_printer", update_req)

    assert updated.mode == "FULL_AUTO"
    assert updated.min_confidence == 0.92
    assert not updated.is_circuit_broken
    assert updated.consecutive_failures == 0

    # Verify Redis is updated
    fetched = await policy_service.get_policy("install_printer")
    assert fetched.mode == "FULL_AUTO"
    assert fetched.min_confidence == 0.92


@pytest.mark.asyncio
async def test_circuit_breaker_tripwire_on_3_failures(policy_service):
    # Set initially to FULL_AUTO
    await policy_service.update_policy("install_printer", AutopilotPolicyUpdateDTO(mode="FULL_AUTO"))

    # Failure 1
    p1 = await policy_service.record_failure("install_printer", error="Port unreachable")
    assert p1.consecutive_failures == 1
    assert not p1.is_circuit_broken
    assert p1.mode == "FULL_AUTO"

    # Failure 2
    p2 = await policy_service.record_failure("install_printer", error="Connection timeout")
    assert p2.consecutive_failures == 2
    assert not p2.is_circuit_broken
    assert p2.mode == "FULL_AUTO"

    # Failure 3 -> CIRCUIT BREAKER TRIPS!
    p3 = await policy_service.record_failure("install_printer", error="Printer offline")
    assert p3.consecutive_failures == 3
    assert p3.is_circuit_broken is True
    assert p3.mode == "ASSISTED"  # Automatically degraded to ASSISTED!


@pytest.mark.asyncio
async def test_circuit_breaker_sliding_window_reset(policy_service, async_session_factory):
    # Set to FULL_AUTO
    await policy_service.update_policy("install_printer", AutopilotPolicyUpdateDTO(mode="FULL_AUTO"))

    # Record 2 failures
    await policy_service.record_failure("install_printer", error="err1")
    await policy_service.record_failure("install_printer", error="err2")

    # Manually age the last_failure_at to 15 minutes ago
    async with async_session_factory() as session:
        pol = await session.get(AutopilotPolicy, "install_printer")
        pol.last_failure_at = datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_BREAKER_WINDOW_SEC + 60)
        await session.commit()

    # Next failure happens outside the 10m window -> resets count to 1 instead of tripping
    p = await policy_service.record_failure("install_printer", error="err3")
    assert p.consecutive_failures == 1
    assert not p.is_circuit_broken
    assert p.mode == "FULL_AUTO"


@pytest.mark.asyncio
async def test_record_success_resets_failures(policy_service):
    await policy_service.record_failure("install_printer", error="err1")
    p1 = await policy_service.get_policy("install_printer")
    assert p1.consecutive_failures == 1

    await policy_service.record_success("install_printer")
    p2 = await policy_service.get_policy("install_printer")
    assert p2.consecutive_failures == 0
