"""Sequential command scheduling tests for WorkflowRunner."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.automation.capabilities import get_default_capability_registry
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import (
    ActionProposal,
    CaseCandidate,
    CaseDecision,
    CaseDecisionState,
    CaseFrame,
    compute_action_plan_hash,
)
from core.automation.persistence import AutomationRepository, canonical_params_hash
from core.automation.runner import RunnerState, WorkflowRunner
from core.automation.workflows import get_default_workflow_registry
from core.database.base import Base
from core.database.models import ActionPreflightRecord

SNAPSHOT_HASH = "f" * 64


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _two_action_aggregate():
    frame = CaseFrame(
        task_id=99,
        snapshot_hash=SNAPSHOT_HASH,
        frame_version="case-frame-v1",
        entities={"pc_name": "WS-99", "connection_type": "network", "printer_address": "10.0.0.9"},
    )
    decision = CaseDecision(
        task_id=99,
        snapshot_hash=SNAPSHOT_HASH,
        frame_id=frame.id,
        router_version="case-router-v1",
        state=CaseDecisionState.selected,
        primary_case_type="printer_connection_request",
        candidates=[CaseCandidate(case_type="printer_connection_request", case_type_version="1.0.0")],
    )
    compiler = WorkflowCompiler(get_default_workflow_registry(), get_default_capability_registry())
    workflow, plan = compiler.compile(frame=frame, decision=decision)
    assert plan is not None
    first = plan.actions[0]
    second = ActionProposal(
        id="action_reset_print_spooler_1",
        capability_key="reset_print_spooler",
        sequence_no=1,
        params={"pc_name": "WS-99"},
        depends_on_action_ids=[first.id],
        risk="medium",
    )
    draft = plan.model_copy(update={"actions": [first, second], "plan_hash": ""})
    plan = draft.model_copy(update={"plan_hash": compute_action_plan_hash(draft)})
    return frame, decision, workflow, plan


async def _persist_and_approve(session_factory):
    repository = AutomationRepository()
    frame, decision, workflow, plan = _two_action_aggregate()
    async with session_factory() as session, session.begin():
        await repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=plan,
        )
        for action in plan.actions:
            session.add(
                ActionPreflightRecord(
                    action_plan_id=plan.id,
                    task_id=plan.task_id,
                    action_id=action.id,
                    capability_key=action.capability_key,
                    snapshot_hash=plan.snapshot_hash,
                    plan_hash=plan.plan_hash,
                    params_hash=canonical_params_hash(action.params),
                    status="passed",
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
    async with session_factory() as session, session.begin():
        approved = await repository.approve_action_plan(
            session,
            action_plan_id=plan.id,
            expected_plan_hash=plan.plan_hash,
            expected_snapshot_hash=SNAPSHOT_HASH,
            operator_username="operator",
        )
    return approved


async def test_runner_publishes_one_command_at_a_time(session_factory) -> None:
    approved = await _persist_and_approve(session_factory)
    runner = WorkflowRunner()

    async with session_factory() as session, session.begin():
        first = await runner.advance(session, action_plan_id=approved.id, initiator="operator")
        assert first.state == RunnerState.command_ready
        assert first.command is not None
        assert first.command.sequence_no == 0
        first.command.status = "succeeded"

    async with session_factory() as session, session.begin():
        second = await runner.advance(session, action_plan_id=approved.id, initiator="operator")
        assert second.state == RunnerState.command_ready
        assert second.command is not None
        assert second.command.sequence_no == 1
        second.command.status = "succeeded"

    async with session_factory() as session, session.begin():
        completed = await runner.advance(session, action_plan_id=approved.id, initiator="operator")
        assert completed.state == RunnerState.completed


@pytest.mark.parametrize("terminal_status", ["failed", "unknown_outcome", "aborted", "skipped"])
async def test_runner_stops_plan_on_unconfirmed_outcome(session_factory, terminal_status: str) -> None:
    approved = await _persist_and_approve(session_factory)
    runner = WorkflowRunner()
    async with session_factory() as session, session.begin():
        started = await runner.advance(session, action_plan_id=approved.id, initiator="operator")
        assert started.command is not None
        started.command.status = terminal_status

    async with session_factory() as session, session.begin():
        stopped = await runner.advance(session, action_plan_id=approved.id, initiator="operator")
        assert stopped.state == RunnerState.needs_review
        assert stopped.reason == f"command_{terminal_status}"
