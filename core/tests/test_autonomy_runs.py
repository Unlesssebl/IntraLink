from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.automation.autonomy import AutonomyFailureReporter, AutonomyRunCoordinator
from core.database.base import Base
from core.database.models import AutonomyCommentOutboxRecord, AutonomyRunRecord
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO, TaskStatusDTO
from core.intraservice.statuses import TaskStatusResolver


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_assignment_epoch_is_durable_and_status_29_is_not_final(session_factory) -> None:
    client = AsyncMock()
    client.get_statuses.return_value = [
        TaskStatusDTO(Id=29, Name="Выполнена", IsFinal=False, IsFixed=True),
        TaskStatusDTO(Id=30, Name="Закрыта", IsFinal=True, IsFixed=True),
    ]
    coordinator = AutonomyRunCoordinator()
    resolver = TaskStatusResolver()
    assigned = TaskDTO(Id=101, StatusId=29, ExecutorIds="777")

    async with session_factory() as session:
        finality = await resolver.resolve(session, client, status_id=29, auth_b64="token")
        assert finality is not None and finality.is_final is False and finality.is_fixed is True
        first = await coordinator.observe(
            session,
            task=assigned,
            bot_user_id=777,
            is_final=finality.is_final,
            baseline=False,
        )
        duplicate = await coordinator.observe(
            session,
            task=assigned,
            bot_user_id=777,
            is_final=False,
            baseline=False,
        )
        await session.commit()

        assert first.created is True
        assert duplicate.run is not None and duplicate.run.id == first.run.id
        assert await session.scalar(select(func.count()).select_from(AutonomyRunRecord)) == 1

        removed = await coordinator.observe(
            session,
            task=TaskDTO(Id=101, StatusId=29, ExecutorIds=""),
            bot_user_id=777,
            is_final=False,
        )
        assert removed.cancelled is True
        assert removed.run is not None and removed.run.state == "cancelled"
        second = await coordinator.observe(
            session,
            task=assigned,
            bot_user_id=777,
            is_final=False,
        )
        await session.commit()
        assert second.created is True
        assert second.run is not None and second.run.assignment_epoch == 2
        assert await session.scalar(select(func.count()).select_from(AutonomyRunRecord)) == 2


async def test_baseline_and_final_assignment_do_not_start_run(session_factory) -> None:
    coordinator = AutonomyRunCoordinator()
    async with session_factory() as session:
        baseline = await coordinator.observe(
            session,
            task=TaskDTO(Id=201, StatusId=29, ExecutorIds="777"),
            bot_user_id=777,
            is_final=False,
            baseline=True,
        )
        final = await coordinator.observe(
            session,
            task=TaskDTO(Id=202, StatusId=30, ExecutorIds="777"),
            bot_user_id=777,
            is_final=True,
            baseline=False,
        )
        await session.commit()
        assert baseline.baseline is True and baseline.run is None
        assert final.run is None
        assert await session.scalar(select(func.count()).select_from(AutonomyRunRecord)) == 0


async def test_missed_assignment_is_recovered_as_cancelled_audit_run(session_factory) -> None:
    coordinator = AutonomyRunCoordinator()
    async with session_factory() as session:
        await coordinator.observe(
            session,
            task=TaskDTO(Id=301, StatusId=2, ExecutorIds=""),
            bot_user_id=777,
            is_final=False,
            lifetime=[TaskLifetimeEventDTO(Id=1, EventKey="e1")],
            bot_display_name="Auto Bot",
            baseline=True,
        )
        recovered = await coordinator.observe(
            session,
            task=TaskDTO(Id=301, StatusId=2, ExecutorIds=""),
            bot_user_id=777,
            is_final=False,
            lifetime=[
                TaskLifetimeEventDTO(Id=1, EventKey="e1"),
                TaskLifetimeEventDTO(Id=2, EventKey="e2", Executors="Auto Bot"),
                TaskLifetimeEventDTO(Id=3, EventKey="e3", Executors="Иванов И.И."),
            ],
            bot_display_name="Auto Bot",
        )
        await session.commit()
        assert recovered.created is True and recovered.cancelled is True
        assert recovered.run is not None
        assert recovered.run.blocker_code == "bot_unassigned_before_poll"


async def test_failure_outbox_is_deduplicated_and_recovers_uncertain_delivery(session_factory) -> None:
    reporter = AutonomyFailureReporter()
    client = AsyncMock()
    client.get_task_lifetime.return_value = []
    client.add_task_comment.side_effect = [TimeoutError("secret token=bad"), True]

    async with session_factory() as session:
        run = AutonomyRunRecord(
            task_id=401,
            bot_user_id=777,
            assignment_epoch=1,
            assignment_event_key="e1",
            state="blocked",
        )
        session.add(run)
        await session.flush()
        first = await reporter.enqueue(
            session, run=run, phase="planning", blocker_code="unsupported_case"
        )
        second = await reporter.enqueue(
            session, run=run, phase="planning", blocker_code="unsupported_case"
        )
        assert first.id == second.id
        await reporter.deliver_pending(session, client=client, auth_b64="token")
        first.next_attempt_at = first.created_at
        await reporter.deliver_pending(session, client=client, auth_b64="token")
        await session.commit()

        stored = await session.get(AutonomyCommentOutboxRecord, first.id)
        assert stored is not None and stored.state == "published"
        assert stored.attempts == 1
        assert "secret" not in (stored.last_error or "")
        assert client.add_task_comment.call_count == 2
        assert client.add_task_comment.call_args.kwargs["is_private"] is True
