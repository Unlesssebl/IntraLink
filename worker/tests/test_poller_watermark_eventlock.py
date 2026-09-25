"""Integration tests for IngestionPoller dual-slice querying, Watermark persistence and Event Lock idempotency."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database.base import Base
from core.database.system_state import (
    WatermarkService,
    set_system_state_session_factory,
)
from core.intraservice.auth import ServiceAuthCredentials
from core.intraservice.dto import TaskDTO
from worker.src.tasks.poller import IngestionPoller


class MockRedisExtended:
    """Mock Redis with key storage, TTL, existence checks and NX support."""

    def __init__(self) -> None:
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int = 0):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def exists(self, key: str):
        return 1 if key in self.store else 0

    async def delete(self, *keys: str):
        for k in keys:
            self.store.pop(k, None)


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


@pytest.mark.asyncio
async def test_poller_watermark_dual_tier_persistence(test_db):
    """Verify poller sets watermark in Redis (autopilot:watermark:ts, autopilot:watermark:task_id) and PostgreSQL."""
    mock_redis = MockRedisExtended()
    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="valid_auth",
        bot_user_id=9999,
        login="bot",
    )

    mock_client = AsyncMock()
    t1 = TaskDTO(Id=501, Name="Ticket 501", StatusId=1, ExecutorIds="")
    t2 = TaskDTO(Id=505, Name="Ticket 505", StatusId=2, ExecutorIds="9999")
    mock_client.get_tasks_by_filter.return_value = [t1]
    mock_client.get_tasks.return_value = [t2]

    watermark_service = WatermarkService(session_factory=test_db, redis_client=mock_redis)

    poller = IngestionPoller(
        service_auth=mock_auth,
        watermark_service=watermark_service,
        client=mock_client,
        redis_client=mock_redis,
        session_factory=test_db,
    )

    with (
        patch("worker.src.tasks.poller.triage_task.kiq", new_callable=AsyncMock) as mock_triage,
        patch("worker.src.tasks.poller.autopilot_task.kiq", new_callable=AsyncMock) as mock_autopilot,
    ):
        result = await poller.poll_step()

        assert result.unique_tasks_count == 2
        assert result.triaged_tasks_count == 1
        assert result.autopilot_tasks_count == 1

        # Verify Redis keys
        assert await mock_redis.get("autopilot:watermark:task_id") == "505"
        ts_val = await mock_redis.get("autopilot:watermark:ts")
        assert ts_val is not None

        # Verify PostgreSQL durable tier
        wm_db = await watermark_service.get_watermark("ingestion_poller")
        assert wm_db.last_task_id == 505
        assert wm_db.last_poll_at is not None


@pytest.mark.asyncio
async def test_poller_event_lock_suppresses_redundant_enqueue(test_db):
    """Verify Event Lock suppresses duplicate enqueues for the same unmodified ticket state."""
    mock_redis = MockRedisExtended()
    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="valid_auth",
        bot_user_id=9999,
        login="bot",
    )

    # Ticket with constant Changed timestamp
    t10 = TaskDTO(Id=710, Name="Unassigned ticket", StatusId=1, ExecutorIds="", Changed="2026-09-25 10:00:00")

    mock_client = AsyncMock()
    mock_client.get_tasks_by_filter.return_value = [t10]
    mock_client.get_tasks.return_value = []

    watermark_service = WatermarkService(session_factory=test_db, redis_client=mock_redis)

    poller = IngestionPoller(
        service_auth=mock_auth,
        watermark_service=watermark_service,
        client=mock_client,
        redis_client=mock_redis,
        session_factory=test_db,
    )

    with patch("worker.src.tasks.poller.triage_task.kiq", new_callable=AsyncMock) as mock_triage:
        # Iteration 1: Ticket is new -> Enqueued
        res1 = await poller.poll_step()
        assert res1.triaged_tasks_count == 1
        assert mock_triage.await_count == 1

        # Iteration 2: Ticket unchanged -> Event Lock blocks duplicate enqueue
        res2 = await poller.poll_step()
        assert res2.triaged_tasks_count == 0
        assert mock_triage.await_count == 1  # Still 1, NOT called again!

        # Iteration 3: Ticket changed (applicant added comment / updated)
        t10_updated = TaskDTO(Id=710, Name="Unassigned ticket", StatusId=1, ExecutorIds="", Changed="2026-09-25 10:01:30")
        mock_client.get_tasks_by_filter.return_value = [t10_updated]

        res3 = await poller.poll_step()
        assert res3.triaged_tasks_count == 1
        assert mock_triage.await_count == 2  # Called again because state changed!


@pytest.mark.asyncio
async def test_poller_single_flight_lock_prevents_race_with_in_flight_worker(test_db):
    """Verify Single-Flight lock (lock:task:{id}) prevents poller from re-enqueuing active in-flight tickets."""
    mock_redis = MockRedisExtended()
    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="valid_auth",
        bot_user_id=9999,
        login="bot",
    )

    t20 = TaskDTO(Id=820, Name="Bot assigned", StatusId=2, ExecutorIds="9999")
    mock_client = AsyncMock()
    mock_client.get_tasks_by_filter.return_value = []
    mock_client.get_tasks.return_value = [t20]

    # Worker has active in-flight lock for task 820
    await mock_redis.set("lock:task:820", "worker_pid_123")

    watermark_service = WatermarkService(session_factory=test_db, redis_client=mock_redis)

    poller = IngestionPoller(
        service_auth=mock_auth,
        watermark_service=watermark_service,
        client=mock_client,
        redis_client=mock_redis,
        session_factory=test_db,
    )

    with patch("worker.src.tasks.poller.autopilot_task.kiq", new_callable=AsyncMock) as mock_autopilot:
        res = await poller.poll_step()

        # Enqueue suppressed because worker is currently executing task 820
        assert res.autopilot_tasks_count == 0
        mock_autopilot.assert_not_called()

        # Once worker finishes and lock is released
        await mock_redis.delete("lock:task:820")

        # Next poll step successfully enqueues
        res_after = await poller.poll_step()
        assert res_after.autopilot_tasks_count == 1
        mock_autopilot.assert_awaited_once_with(task_id=820)
