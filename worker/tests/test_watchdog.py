"""Unit and integration tests for InactivityWatchdog (Edge Case 12)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from worker.src.tasks.watchdog import (
    InactivityWatchdog,
    inactivity_watchdog_task,
    set_watchdog_client,
    set_watchdog_redis_client,
    set_watchdog_service_auth,
)


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


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock(spec=IntraServiceClient)
    client.update_task.return_value = True
    return client


@pytest.fixture
def auth() -> ServiceAuthCredentials:
    return ServiceAuthCredentials(
        auth_b64="Ym90OnBhc3M=",
        bot_user_id=999,
        login="service_bot",
    )


@pytest.mark.asyncio
async def test_watchdog_waiting_within_48_hours(mock_client, mock_redis, auth):
    """Ticket suspended 20 hours ago is within normal waiting window."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    changed_time = now - timedelta(hours=20)

    task = TaskDTO(
        Id=701,
        Name="Тестовая заявка",
        StatusId=6,
        StatusName="Приостановлена",
        Changed=changed_time.isoformat(),
    )

    watchdog = InactivityWatchdog()
    res = await watchdog.evaluate_and_process_ticket(
        task=task,
        auth=auth,
        client=mock_client,
        redis_conn=mock_redis,
        now=now,
    )

    assert res["action"] == "waiting"
    assert res["task_id"] == 701
    assert res["elapsed_hours"] == 20.0
    mock_client.update_task.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_sends_reminder_after_48_hours(mock_client, mock_redis, auth):
    """Ticket suspended 50 hours ago triggers courtesy reminder and sets anti-spam Redis flag."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    changed_time = now - timedelta(hours=50)

    task = TaskDTO(
        Id=702,
        Name="Тестовая заявка",
        StatusId=6,
        StatusName="Приостановлена",
        Changed=changed_time.isoformat(),
    )

    watchdog = InactivityWatchdog()
    res = await watchdog.evaluate_and_process_ticket(
        task=task,
        auth=auth,
        client=mock_client,
        redis_conn=mock_redis,
        now=now,
    )

    assert res["action"] == "reminder_sent"
    assert res["elapsed_hours"] == 50.0

    # Verify public reminder and internal audit were sent
    calls = mock_client.update_task.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["is_private"] is False
    assert "Напоминаем о необходимости" in calls[0].kwargs["comment"]
    assert calls[1].kwargs["is_private"] is True
    assert "Напоминание заявителю" in calls[1].kwargs["comment"]

    # Verify anti-spam flag set in Redis
    assert await mock_redis.exists("watchdog:reminder_sent:702") == 1


@pytest.mark.asyncio
async def test_watchdog_anti_spam_prevents_duplicate_reminders(mock_client, mock_redis, auth):
    """If anti-spam flag is present in Redis, no duplicate reminder is sent."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    changed_time = now - timedelta(hours=70)

    task = TaskDTO(
        Id=703,
        Name="Тестовая заявка",
        StatusId=6,
        StatusName="Приостановлена",
        Changed=changed_time.isoformat(),
    )

    # Pre-set reminder flag
    await mock_redis.set("watchdog:reminder_sent:703", "1")

    watchdog = InactivityWatchdog()
    res = await watchdog.evaluate_and_process_ticket(
        task=task,
        auth=auth,
        client=mock_client,
        redis_conn=mock_redis,
        now=now,
    )

    assert res["action"] == "reminder_already_sent"
    mock_client.update_task.assert_not_called()


@pytest.mark.asyncio
async def test_watchdog_auto_cancels_after_120_hours(mock_client, mock_redis, auth):
    """Ticket suspended 125 hours ago is automatically cancelled (Status 30)."""
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    changed_time = now - timedelta(hours=125)

    task = TaskDTO(
        Id=704,
        Name="Тестовая заявка",
        StatusId=6,
        StatusName="Приостановлена",
        Changed=changed_time.isoformat(),
    )

    await mock_redis.set("watchdog:reminder_sent:704", "1")

    watchdog = InactivityWatchdog()
    res = await watchdog.evaluate_and_process_ticket(
        task=task,
        auth=auth,
        client=mock_client,
        redis_conn=mock_redis,
        now=now,
    )

    assert res["action"] == "auto_cancelled"
    assert res["elapsed_hours"] == 125.0

    # Verify direct status transition to 30 (Cancelled)
    calls = mock_client.update_task.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["status_id"] == 30
    assert calls[0].kwargs["is_private"] is False
    assert "в связи с отсутствием ответа" in calls[0].kwargs["comment"]
    assert calls[1].kwargs["is_private"] is True
    assert "Авто-закрытие по таймауту" in calls[1].kwargs["comment"]

    # Verify reminder flag was cleaned up
    assert await mock_redis.exists("watchdog:reminder_sent:704") == 0


@pytest.mark.asyncio
async def test_watchdog_taskiq_task_batch_execution(mock_client, mock_redis):
    """Verify taskiq task fetches status 6 tickets and coordinates batch execution."""
    now = datetime.now(UTC)
    t1 = TaskDTO(Id=710, StatusId=6, Changed=(now - timedelta(hours=10)).isoformat())
    t2 = TaskDTO(Id=711, StatusId=6, Changed=(now - timedelta(hours=50)).isoformat())
    t3 = TaskDTO(Id=712, StatusId=6, Changed=(now - timedelta(hours=130)).isoformat())

    mock_client.get_tasks.return_value = [t1, t2, t3]

    mock_auth = AsyncMock(spec=ServiceAuthBootstrap)
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="Ym90OnBhc3M=",
        bot_user_id=999,
        login="service_bot",
    )

    set_watchdog_client(mock_client)
    set_watchdog_service_auth(mock_auth)
    set_watchdog_redis_client(mock_redis)

    res = await inactivity_watchdog_task()

    assert res["status"] == "completed"
    assert res["inspected"] == 3
    assert res["reminders_sent"] == 1
    assert res["auto_cancelled"] == 1

    set_watchdog_client(None)
    set_watchdog_service_auth(None)
    set_watchdog_redis_client(None)
