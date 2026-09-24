"""Unit and integration tests for IngestionPoller, ServiceAuthBootstrap, and routing logic."""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.crypto import set_fernet
from core.database.base import Base
from core.database.system_state import (
    WatermarkService,
    set_system_state_session_factory,
)
from core.intraservice.dto import TaskDTO
from worker.src.broker import QUEUE_DEFAULT, broker
from worker.src.services.auth import (
    ServiceAuthBootstrap,
    ServiceAuthCredentials,
    ServiceAuthError,
)
from worker.src.tasks.poller import (
    IngestionPoller,
    get_executor_ids_list,
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


@pytest.fixture
def crypto_fernet():
    key = Fernet.generate_key()
    cipher = Fernet(key)
    set_fernet(cipher)
    yield cipher
    set_fernet(None)


def test_poller_tasks_registration():
    """Verify poller, triage and autopilot tasks are registered with the Taskiq broker."""
    tasks = broker.get_all_tasks()
    assert "poll_queue_task" in tasks
    assert "triage_task" in tasks
    assert "autopilot_task" in tasks

    assert tasks["poll_queue_task"].labels.get("queue_name") == QUEUE_DEFAULT
    assert tasks["triage_task"].labels.get("queue_name") == QUEUE_DEFAULT
    assert tasks["autopilot_task"].labels.get("queue_name") == QUEUE_DEFAULT


def test_get_executor_ids_list_parsing():
    """Verify parsing various formats of ExecutorIds strings into integer lists."""
    task_empty = TaskDTO(Id=1, Name="Task 1", StatusId=1)
    assert get_executor_ids_list(task_empty) == []

    task_single = TaskDTO(Id=2, Name="Task 2", StatusId=1, ExecutorIds="9999")
    assert get_executor_ids_list(task_single) == [9999]

    task_multiple = TaskDTO(Id=3, Name="Task 3", StatusId=1, ExecutorIds="42, 9999, 100")
    assert get_executor_ids_list(task_multiple) == [42, 9999, 100]

    task_dirty = TaskDTO(Id=4, Name="Task 4", StatusId=1, ExecutorIds=" , invalid, 55 , ")
    assert get_executor_ids_list(task_dirty) == [55]


@pytest.mark.asyncio
async def test_service_auth_bootstrap_live_verification(crypto_fernet):
    """Verify live credentials verification and encrypted caching in Redis."""
    mock_client = AsyncMock()
    mock_client.verify_credentials.return_value = ("Ym90X3VzZXI6cGFzcw==", 9999)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Cache miss

    service_auth = ServiceAuthBootstrap()

    with patch.dict(os.environ, {
        "INTRASERVICE_BOT_LOGIN": "autopilot_bot",
        "INTRASERVICE_BOT_PASSWORD": "secure_bot_password",
    }):
        creds = await service_auth.bootstrap_auth(
            client=mock_client,
            redis_client=mock_redis,
        )

        assert creds.auth_b64 == "Ym90X3VzZXI6cGFzcw=="
        assert creds.bot_user_id == 9999
        assert creds.login == "autopilot_bot"

        mock_client.verify_credentials.assert_awaited_once_with(
            login="autopilot_bot",
            password="secure_bot_password",
        )

        # Verify Redis caching with Fernet encryption
        mock_redis.set.assert_any_call(ServiceAuthBootstrap.KEY_SERVICE_USER_ID, "9999")
        mock_redis.set.assert_any_call(ServiceAuthBootstrap.KEY_SERVICE_LOGIN, "autopilot_bot")
        assert mock_redis.set.call_count >= 3


@pytest.mark.asyncio
async def test_service_auth_bootstrap_from_redis_cache(crypto_fernet):
    """Verify restoring service auth from encrypted Redis vault without calling API."""
    service_auth = ServiceAuthBootstrap()

    plain_auth = "Ym90X3VzZXI6cGFzcw=="
    enc_token = crypto_fernet.encrypt(plain_auth.encode()).decode()

    mock_redis = AsyncMock()
    mock_redis.get.side_effect = lambda k: {
        ServiceAuthBootstrap.KEY_SERVICE_AUTH_B64: enc_token,
        ServiceAuthBootstrap.KEY_SERVICE_USER_ID: "9999",
        ServiceAuthBootstrap.KEY_SERVICE_LOGIN: "autopilot_bot",
    }.get(k)

    mock_client = AsyncMock()

    creds = await service_auth.bootstrap_auth(
        client=mock_client,
        redis_client=mock_redis,
    )

    assert creds.auth_b64 == plain_auth
    assert creds.bot_user_id == 9999
    assert creds.login == "autopilot_bot"

    # API was NOT called because Redis cache had credentials
    mock_client.verify_credentials.assert_not_called()


@pytest.mark.asyncio
async def test_service_auth_missing_credentials_raises_error():
    """Verify ServiceAuthError is raised when login or password are not provided."""
    service_auth = ServiceAuthBootstrap()
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ServiceAuthError, match="Service bot credentials missing"):
            await service_auth.bootstrap_auth(redis_client=mock_redis)


@pytest.mark.asyncio
async def test_service_auth_invalid_credentials_raises_error():
    """Verify ServiceAuthError is raised when API rejects bot credentials."""
    mock_client = AsyncMock()
    mock_client.verify_credentials.return_value = (None, None)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None

    service_auth = ServiceAuthBootstrap()

    with patch.dict(os.environ, {
        "INTRASERVICE_BOT_LOGIN": "wrong_bot",
        "INTRASERVICE_BOT_PASSWORD": "wrong_password",
    }):
        with pytest.raises(ServiceAuthError, match="Failed to authenticate"):
            await service_auth.bootstrap_auth(
                client=mock_client,
                redis_client=mock_redis,
            )


@pytest.mark.asyncio
async def test_ingestion_poller_dual_slice_and_routing(test_db):
    """Verify poller executes dual-slice query, deduplicates tickets and routes properly."""
    # Setup mocks
    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="valid_auth",
        bot_user_id=9999,
        login="bot",
    )

    mock_watermark_service = WatermarkService(session_factory=test_db)
    # Pre-populate watermark
    await mock_watermark_service.set_watermark(
        key="ingestion_poller",
        last_poll_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc),
        last_task_id=100,
    )

    # Slice 1: Filter 984 -> Task 101 (unassigned), Task 102 (assigned to engineer 42)
    t101 = TaskDTO(Id=101, Name="Unassigned ticket", StatusId=1, ExecutorIds="")
    t102 = TaskDTO(Id=102, Name="Engineer ticket", StatusId=2, ExecutorIds="42")

    # Slice 2: ChangedMoreThan -> Task 101 (duplicate), Task 103 (assigned to bot 9999)
    t103 = TaskDTO(Id=103, Name="Bot assigned ticket", StatusId=2, ExecutorIds="9999")

    mock_client = AsyncMock()
    mock_client.get_tasks_by_filter.return_value = [t101, t102]
    mock_client.get_tasks.return_value = [t101, t103]

    poller = IngestionPoller(
        service_auth=mock_auth,
        watermark_service=mock_watermark_service,
        client=mock_client,
        session_factory=test_db,
    )

    with (
        patch("worker.src.tasks.poller.triage_task.kiq", new_callable=AsyncMock) as mock_triage,
        patch("worker.src.tasks.poller.autopilot_task.kiq", new_callable=AsyncMock) as mock_autopilot,
    ):
        result = await poller.poll_step()

        assert result.filter_tasks_count == 2
        assert result.changed_tasks_count == 2
        assert result.unique_tasks_count == 3  # 101, 102, 103
        assert result.triaged_tasks_count == 1  # 101 (unassigned)
        assert result.autopilot_tasks_count == 1  # 103 (bot assigned)
        assert result.next_interval_sec == 30.0
        assert result.consecutive_errors == 0
        assert result.error is None

        # Verify triage queue called for 101
        mock_triage.assert_awaited_once_with(task_id=101)

        # Verify autopilot queue called for 103
        mock_autopilot.assert_awaited_once_with(task_id=103)

        # Verify filter queries
        mock_client.get_tasks_by_filter.assert_awaited_once_with(
            filter_id=984,
            auth_b64="valid_auth",
        )
        mock_client.get_tasks.assert_awaited_once()
        changed_filter_call = mock_client.get_tasks.call_args[1]["filters"]
        assert "ChangedMoreThan" in changed_filter_call

        # Verify watermark updated to max task id (103)
        updated_wm = await mock_watermark_service.get_watermark("ingestion_poller")
        assert updated_wm.last_task_id == 103
        assert updated_wm.last_poll_at is not None


@pytest.mark.asyncio
async def test_ingestion_poller_exponential_backoff_cascade(test_db):
    """Verify exponential backoff progression (30s -> 60s -> 120s) and recovery to 30s."""
    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="valid_auth",
        bot_user_id=9999,
        login="bot",
    )

    mock_watermark_service = WatermarkService(session_factory=test_db)
    mock_client = AsyncMock()

    poller = IngestionPoller(
        service_auth=mock_auth,
        watermark_service=mock_watermark_service,
        client=mock_client,
        session_factory=test_db,
    )

    # 1. First failure -> 60s
    mock_client.get_tasks_by_filter.side_effect = TimeoutError("IntraService unreachable")
    res1 = await poller.poll_step()
    assert res1.next_interval_sec == 60.0
    assert res1.consecutive_errors == 1
    assert "IntraService fetch error" in res1.error

    # 2. Second failure -> 120s (max ceiling)
    res2 = await poller.poll_step()
    assert res2.next_interval_sec == 120.0
    assert res2.consecutive_errors == 2

    # 3. Third failure -> remains at 120s max backoff
    res3 = await poller.poll_step()
    assert res3.next_interval_sec == 120.0
    assert res3.consecutive_errors == 3

    # 4. Recovery on success -> resets back to 30s
    mock_client.get_tasks_by_filter.side_effect = None
    mock_client.get_tasks_by_filter.return_value = []
    mock_client.get_tasks.return_value = []

    res4 = await poller.poll_step()
    assert res4.next_interval_sec == 30.0
    assert res4.consecutive_errors == 0
    assert res4.error is None
