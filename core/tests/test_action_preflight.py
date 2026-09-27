"""Fail-closed ActionPlan preflight tests."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.automation.capabilities import (
    CapabilityExecution,
    CapabilityExecutionContext,
    CapabilityOutcome,
    CapabilityPreflight,
    CapabilityRegistry,
    CapabilityRisk,
    CapabilitySpec,
    IdempotencyMode,
    PreflightStatus,
    get_default_capability_registry,
)
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import CaseCandidate, CaseDecision, CaseDecisionState, CaseFrame
from core.automation.persistence import AutomationRepository
from core.automation.preflight import ActionPreflightService
from core.automation.workflows import get_default_workflow_registry
from core.database.base import Base

SNAPSHOT_HASH = "9" * 64


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _printer_plan():
    frame = CaseFrame(
        task_id=7,
        snapshot_hash=SNAPSHOT_HASH,
        frame_version="case-frame-v1",
        entities={"pc_name": "WS-7", "connection_type": "network", "printer_address": "10.0.0.7"},
    )
    decision = CaseDecision(
        task_id=7,
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
    return frame, decision, workflow, plan


async def test_disabled_capability_cannot_pass_preflight_or_approval(session_factory) -> None:
    frame, decision, workflow, plan = _printer_plan()
    repository = AutomationRepository()
    service = ActionPreflightService(get_default_capability_registry())
    async with session_factory() as session, session.begin():
        await repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=plan,
        )
        records = await service.run_plan(session, action_plan_id=plan.id)
        assert records[0].status == "failed"
        assert records[0].error_message == "capability_disabled"

    async with session_factory() as session, session.begin():
        with pytest.raises(ValueError, match="action_preflight_not_passed"):
            await repository.approve_action_plan(
                session,
                action_plan_id=plan.id,
                expected_plan_hash=plan.plan_hash,
                expected_snapshot_hash=plan.snapshot_hash,
                operator_username="operator",
            )


class _PassingExecutor:
    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        return CapabilityPreflight(status=PreflightStatus.passed, checks=["test"])

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        return CapabilityExecution(outcome=CapabilityOutcome.succeeded)


async def test_only_matching_unexpired_passed_preflight_allows_approval(session_factory) -> None:
    frame, decision, workflow, plan = _printer_plan()
    registry = CapabilityRegistry(
        (
            CapabilitySpec(
                key="install_printer",
                version="test",
                name="test",
                description="test",
                executor="test",
                required_params=("pc_name", "connection_type"),
                risk=CapabilityRisk.medium,
                idempotency=IdempotencyMode.key_guarded,
                is_mutating=True,
            ),
        )
    )
    registry.bind_executor("install_printer", _PassingExecutor())
    repository = AutomationRepository()
    service = ActionPreflightService(registry)
    async with session_factory() as session, session.begin():
        await repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=plan,
        )
        records = await service.run_plan(session, action_plan_id=plan.id)
        assert records[0].status == "passed"

    async with session_factory() as session, session.begin():
        approved = await repository.approve_action_plan(
            session,
            action_plan_id=plan.id,
            expected_plan_hash=plan.plan_hash,
            expected_snapshot_hash=plan.snapshot_hash,
            operator_username="operator",
        )
        assert approved.state.value == "approved"
