"""Persistence and approval-binding tests for the ADR 0006 aggregate."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.automation.capabilities import get_default_capability_registry
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import (
    ActionPlanState,
    CaseCandidate,
    CaseDecision,
    CaseDecisionState,
    CaseFrame,
)
from core.automation.persistence import AutomationRepository, canonical_params_hash
from core.automation.workflows import get_default_workflow_registry
from core.database.base import Base
from core.database.models import ActionPreflightRecord

SNAPSHOT_HASH = "d" * 64


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _aggregate() -> tuple[CaseFrame, CaseDecision, object, object]:
    frame = CaseFrame(
        task_id=142135,
        snapshot_hash=SNAPSHOT_HASH,
        frame_version="case-frame-v1",
        entities={
            "pc_name": "WS-142135",
            "connection_type": "network",
            "printer_address": "10.10.20.30",
        },
    )
    decision = CaseDecision(
        task_id=frame.task_id,
        snapshot_hash=frame.snapshot_hash,
        frame_id=frame.id,
        router_version="case-router-v1",
        state=CaseDecisionState.selected,
        primary_case_type="printer_connection_request",
        candidates=[CaseCandidate(case_type="printer_connection_request", case_type_version="1.0.0")],
    )
    compiler = WorkflowCompiler(get_default_workflow_registry(), get_default_capability_registry())
    workflow, action_plan = compiler.compile(frame=frame, decision=decision)
    assert action_plan is not None
    return frame, decision, workflow, action_plan


def _passed_preflight(plan) -> ActionPreflightRecord:
    action = plan.actions[0]
    return ActionPreflightRecord(
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


async def test_round_trip_preserves_canonical_aggregate(session_factory) -> None:
    repository = AutomationRepository()
    frame, decision, workflow, action_plan = _aggregate()
    async with session_factory() as session, session.begin():
        await repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=action_plan,
        )
        session.add(_passed_preflight(action_plan))

    async with session_factory() as session:
        restored = await repository.latest_for_task(session, frame.task_id)

    assert restored is not None
    assert restored.frame == frame
    assert restored.decision == decision
    assert restored.workflow_plan == workflow
    assert restored.action_plan == action_plan


async def test_approval_binds_command_to_plan_snapshot_action_and_params(session_factory) -> None:
    repository = AutomationRepository()
    frame, decision, workflow, action_plan = _aggregate()
    async with session_factory() as session, session.begin():
        await repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=action_plan,
        )
        session.add(_passed_preflight(action_plan))
    async with session_factory() as session, session.begin():
        approved = await repository.approve_action_plan(
            session,
            action_plan_id=action_plan.id,
            expected_plan_hash=action_plan.plan_hash,
            expected_snapshot_hash=frame.snapshot_hash,
            operator_username="operator",
        )
        command = repository.command_for_action(
            plan=approved,
            action=approved.actions[0],
            initiator="operator",
        )
        session.add(command)

    assert approved.state == ActionPlanState.approved
    assert command.action_plan_id == action_plan.id
    assert command.action_id == approved.actions[0].id
    assert command.capability_key == "install_printer"
    assert command.snapshot_hash == frame.snapshot_hash
    assert command.params_hash == canonical_params_hash(approved.actions[0].params)


async def test_stale_snapshot_cannot_be_approved(session_factory) -> None:
    repository = AutomationRepository()
    frame, decision, workflow, action_plan = _aggregate()
    async with session_factory() as session, session.begin():
        await repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=action_plan,
        )
    async with session_factory() as session, session.begin():
        with pytest.raises(ValueError, match="stale_ticket_snapshot"):
            await repository.approve_action_plan(
                session,
                action_plan_id=action_plan.id,
                expected_plan_hash=action_plan.plan_hash,
                expected_snapshot_hash="e" * 64,
                operator_username="operator",
            )


async def test_secret_is_rejected_instead_of_persisted(session_factory) -> None:
    repository = AutomationRepository()
    frame, decision, workflow, action_plan = _aggregate()
    unsafe_frame = frame.model_copy(update={"entities": {**frame.entities, "password": "DoNotPersist1!"}})
    async with session_factory() as session, session.begin():
        with pytest.raises(ValueError, match="must not contain secrets"):
            await repository.save_analysis(
                session,
                frame=unsafe_frame,
                decision=decision,
                workflow_plan=workflow,
                action_plan=action_plan,
            )
