"""Unit and integration tests for Knowledge Base sync Taskiq job."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database.base import Base
from core.database.models import CommandRecord
from core.rag.sync import KBSyncStatsDTO
from worker.src.broker import QUEUE_RAG_COMPUTE, broker
from worker.src.tasks.command_dispatcher import (
    dispatch_command_task,
)
from worker.src.tasks.command_dispatcher import (
    set_session_factory as set_dispatcher_session_factory,
)
from worker.src.tasks.sync_kb import (
    set_session_factory as set_sync_kb_session_factory,
)
from worker.src.tasks.sync_kb import (
    sync_closed_tickets_task,
    sync_kb_task,
)


@pytest.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    set_dispatcher_session_factory(session_factory)
    set_sync_kb_session_factory(session_factory)

    yield session_factory

    set_dispatcher_session_factory(None)
    set_sync_kb_session_factory(None)
    await engine.dispose()


def test_sync_kb_task_registration_and_schedule():
    """Verify task is registered with rag_compute queue and daily 02:00 cron schedule."""
    all_tasks = broker.get_all_tasks()
    assert "sync_kb_task" in all_tasks

    task_obj = all_tasks["sync_kb_task"]
    assert task_obj.task_name == "sync_kb_task"
    assert task_obj.labels.get("queue_name") == QUEUE_RAG_COMPUTE

    # Verify daily 02:00 cron schedule in task labels
    schedules = task_obj.labels.get("schedule", [])
    assert len(schedules) > 0

    cron_schedules = [s.get("cron") for s in schedules if "cron" in s]
    assert "0 2 * * *" in cron_schedules


@pytest.mark.asyncio
async def test_sync_kb_task_execution(test_db):
    """Verify sync_kb_task executes successfully with isolated session."""
    with patch(
        "worker.src.tasks.sync_kb.KnowledgeBaseSyncService.sync_incremental",
        new_callable=AsyncMock,
    ) as mock_sync:
        mock_sync.return_value = KBSyncStatsDTO(
            status="succeeded",
            processed=15,
            indexed=10,
            skipped_low_quality=3,
            skipped_duplicates=2,
        )

        res = await sync_kb_task(hours=24, quota_per_service=30, ai_eval=False)

        assert res["status"] == "succeeded"
        assert res["processed"] == 15
        assert res["indexed"] == 10
        assert res["skipped_low_quality"] == 3
        mock_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_closed_tickets_invoked_via_command_dispatcher(test_db):
    """Verify CommandRecord with action 'sync_kb' executes sync_closed_tickets_task via dispatcher."""
    cmd_id = uuid.uuid4()

    async with test_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key=f"sync-cmd-{cmd_id}",
            action="sync_kb",
            executor="worker",
            status="pending",
            params_json={"hours": 48, "quota_per_service": 30},
            target_json={},
            initiator="admin",
        )
        session.add(cmd)
        await session.commit()

    with patch(
        "worker.src.tasks.sync_kb.KnowledgeBaseSyncService.sync_incremental",
        new_callable=AsyncMock,
    ) as mock_sync:
        mock_sync.return_value = KBSyncStatsDTO(
            status="succeeded",
            processed=5,
            indexed=4,
            skipped_existing=1,
        )

        result = await dispatch_command_task(cmd_id)

        assert result["status"] == "succeeded"
        assert result["result"]["indexed"] == 4
        assert result["result"]["processed"] == 5


@pytest.mark.asyncio
async def test_sync_closed_tickets_task_direct_call(test_db):
    """Verify direct invocation of sync_closed_tickets_task helper."""
    with patch(
        "worker.src.tasks.sync_kb.KnowledgeBaseSyncService.sync_incremental",
        new_callable=AsyncMock,
    ) as mock_sync:
        mock_sync.return_value = KBSyncStatsDTO(
            status="succeeded",
            processed=2,
            indexed=2,
        )

        res = await sync_closed_tickets_task(batch_size=50, hours=12)
        assert res["status"] == "succeeded"
        assert res["indexed"] == 2
