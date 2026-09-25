"""Unit and integration tests for AutopilotCircuitBreaker sliding window and tripwire."""

import json
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.autopilot.circuit_breaker import (
    CIRCUIT_BREAKER_WINDOW_SEC,
    AutopilotCircuitBreaker,
)
from core.autopilot.dto import AutopilotPolicyUpdateDTO
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base
from core.database.models import AutopilotPolicy, SystemState


class MockRedisWithSortedSets:
    """Mock Redis client supporting strings, sets and sorted sets."""

    def __init__(self) -> None:
        self.store = {}
        self.zsets = {}
        self.publishes = []

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0):
        self.store[key] = value
        return True

    async def delete(self, *keys: str):
        for k in keys:
            self.store.pop(k, None)
            self.zsets.pop(k, None)

    async def zadd(self, key: str, mapping: dict):
        z = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            z[member] = float(score)
        return len(mapping)

    async def zremrangebyscore(self, key: str, min_score: float, max_score: float):
        z = self.zsets.get(key, {})
        to_del = [m for m, s in z.items() if min_score <= s <= max_score]
        for m in to_del:
            del z[m]
        return len(to_del)

    async def zcard(self, key: str):
        return len(self.zsets.get(key, {}))

    async def expire(self, key: str, seconds: int):
        return True

    async def publish(self, channel: str, message: str):
        self.publishes.append((channel, message))
        return 1


@pytest.fixture
async def async_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest.fixture
def mock_redis() -> MockRedisWithSortedSets:
    return MockRedisWithSortedSets()


@pytest.fixture
def circuit_breaker(async_session_factory, mock_redis) -> AutopilotCircuitBreaker:
    return AutopilotCircuitBreaker(
        session_factory=async_session_factory,
        redis_client=mock_redis,
    )


@pytest.mark.asyncio
async def test_circuit_breaker_trips_to_assisted_on_3_failures(
    circuit_breaker, async_session_factory, mock_redis
):
    """Verify circuit breaker trips from FULL_AUTO to ASSISTED on 3 consecutive failures."""
    scenario_key = "install_printer"

    # Pre-configure policy as FULL_AUTO
    async with async_session_factory() as session:
        pol = AutopilotPolicy(
            scenario_key=scenario_key,
            mode="FULL_AUTO",
            min_confidence=0.85,
            consecutive_failures=0,
            is_circuit_broken=False,
        )
        session.add(pol)
        await session.commit()

    # Failure 1
    s1 = await circuit_breaker.record_failure(scenario_key, error="Network timeout", task_id=101)
    assert s1.consecutive_failures == 1
    assert not s1.is_circuit_broken
    assert s1.mode == "FULL_AUTO"

    # Failure 2
    s2 = await circuit_breaker.record_failure(scenario_key, error="Host offline", task_id=102)
    assert s2.consecutive_failures == 2
    assert not s2.is_circuit_broken
    assert s2.mode == "FULL_AUTO"

    # Failure 3 -> TRIPS!
    s3 = await circuit_breaker.record_failure(scenario_key, error="Print spooler crashed", task_id=103)
    assert s3.consecutive_failures == 3
    assert s3.is_circuit_broken is True
    assert s3.mode == "ASSISTED"

    # Verify PostgreSQL state
    async with async_session_factory() as session:
        pol_db = await session.get(AutopilotPolicy, scenario_key)
        assert pol_db.mode == "ASSISTED"
        assert pol_db.is_circuit_broken is True
        assert pol_db.consecutive_failures == 3

        # Verify SystemState alert event record
        stmt = select(SystemState).where(SystemState.key == f"cb_alert:{scenario_key}")
        alert_event = (await session.execute(stmt)).scalar_one_or_none()
        assert alert_event is not None
        assert alert_event.state_data["event"] == "circuit_breaker_tripped"
        assert alert_event.state_data["consecutive_failures"] == 3
        assert alert_event.state_data["last_error"] == "Print spooler crashed"
        assert alert_event.last_task_id == 103

    # Verify Redis cb:alert:{scenario_key} and pub/sub
    alert_raw = await mock_redis.get(f"cb:alert:{scenario_key}")
    assert alert_raw is not None
    alert_json = json.loads(alert_raw)
    assert alert_json["event"] == "circuit_breaker_tripped"
    assert alert_json["failures"] == 3

    assert len(mock_redis.publishes) >= 1
    channel, msg = mock_redis.publishes[-1]
    assert channel == "autopilot:events"
    assert "circuit_breaker_tripped" in msg


@pytest.mark.asyncio
async def test_circuit_breaker_reset_on_success(circuit_breaker, async_session_factory, mock_redis):
    """Verify record_success clears Redis error window, alerts and resets failure counter."""
    scenario_key = "ad_password_reset"

    # Record 2 failures
    await circuit_breaker.record_failure(scenario_key, error="LDAP error 1", task_id=201)
    await circuit_breaker.record_failure(scenario_key, error="LDAP error 2", task_id=202)

    st_before = await circuit_breaker.get_status(scenario_key)
    assert st_before.consecutive_failures == 2

    # Record success
    st_after = await circuit_breaker.record_success(scenario_key)
    assert st_after.consecutive_failures == 0
    assert not st_after.is_circuit_broken

    # Verify Redis error window cleared
    zcard = await mock_redis.zcard(f"cb:errors:{scenario_key}")
    assert zcard == 0

    # Verify DB reset
    async with async_session_factory() as session:
        pol_db = await session.get(AutopilotPolicy, scenario_key)
        assert pol_db.consecutive_failures == 0
        assert pol_db.is_circuit_broken is False


@pytest.mark.asyncio
async def test_circuit_breaker_sliding_window_expiration(circuit_breaker, async_session_factory):
    """Verify failures outside 10-minute window expire and do not cause premature trip."""
    scenario_key = "account_lock"

    # Pre-configure policy as FULL_AUTO
    async with async_session_factory() as session:
        pol = AutopilotPolicy(
            scenario_key=scenario_key,
            mode="FULL_AUTO",
            min_confidence=0.85,
        )
        session.add(pol)
        await session.commit()

    # Record 2 failures
    await circuit_breaker.record_failure(scenario_key, error="err1", task_id=301)
    await circuit_breaker.record_failure(scenario_key, error="err2", task_id=302)

    # Manually age the failure in DB past 10 minutes
    async with async_session_factory() as session:
        pol_db = await session.get(AutopilotPolicy, scenario_key)
        pol_db.last_failure_at = datetime.now(timezone.utc) - timedelta(seconds=CIRCUIT_BREAKER_WINDOW_SEC + 30)
        await session.commit()

    # Next failure happens outside window -> counter resets to 1
    s3 = await circuit_breaker.record_failure(scenario_key, error="err3", task_id=303)
    assert s3.consecutive_failures == 1
    assert not s3.is_circuit_broken
    assert s3.mode == "FULL_AUTO"
