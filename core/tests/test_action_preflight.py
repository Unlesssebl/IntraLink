"""Fail-closed ActionPlan preflight tests."""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy import select
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
        assert records[0].error_message == "printer_executor_unavailable"


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
        assert approved.state.value in {"authorized", "approved"}


class _InteractiveExecutor:
    def __init__(self, reject_selection: bool = False) -> None:
        self.reject_selection = reject_selection

    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        target_val = params.get("target_server")
        if not target_val:
            from core.automation.capabilities import HumanActionCandidate, HumanActionProposal

            proposal = HumanActionProposal(
                action_type="single_choice",
                target_param="target_server",
                prompt="Выберите целевой сервер",
                check_key="target_server_check",
                candidates=(
                    HumanActionCandidate(
                        candidate_id="srv_1",
                        label="Server 1",
                        score=0.9,
                        server_value="srv-01.corp",
                    ),
                    HumanActionCandidate(
                        candidate_id="srv_2",
                        label="Server 2",
                        score=0.8,
                        server_value="srv-02.corp",
                    ),
                ),
            )
            return CapabilityPreflight(
                status=PreflightStatus.action_required,
                checks=[{"key": "target_server_check", "status": "action_required"}],
                human_action=proposal,
            )
        return CapabilityPreflight(
            status=PreflightStatus.passed,
            checks=[{"key": "target_server_check", "status": "passed"}],
        )

    async def execute(self, params: dict[str, Any], *, context: CapabilityExecutionContext) -> CapabilityExecution:
        return CapabilityExecution(outcome=CapabilityOutcome.succeeded)

    async def validate_human_action(self, target_param: str, value: Any) -> bool:
        if self.reject_selection:
            return False
        return target_param == "target_server" and str(value).startswith("srv-")


async def test_human_action_preflight_and_resolution_saga_success(session_factory) -> None:
    from core.automation.contracts import ActionProposal, compute_action_plan_hash
    from core.automation.resolution import (
        HumanActionConflictError,
        HumanActionResolutionService,
    )
    from core.database.models import HumanActionRequestRecord

    frame, decision, workflow, base_plan = _printer_plan()
    action = ActionProposal(
        id="action_interactive_0",
        sequence_no=1,
        capability_key="interactive_op",
        params={},
    )
    draft = base_plan.model_copy(update={"actions": [action]})
    plan = draft.model_copy(update={"plan_hash": compute_action_plan_hash(draft)})

    registry = CapabilityRegistry(
        (
            CapabilitySpec(
                key="interactive_op",
                version="1.0.0",
                name="Interactive Operation",
                description="test",
                executor="test",
                risk=CapabilityRisk.medium,
                idempotency=IdempotencyMode.key_guarded,
                is_mutating=True,
            ),
        )
    )
    executor = _InteractiveExecutor()
    registry.bind_executor("interactive_op", executor)
    repository = AutomationRepository()
    preflight_service = ActionPreflightService(registry)
    resolution_service = HumanActionResolutionService(registry, repository, preflight_service)

    async with session_factory() as session, session.begin():
        await repository.save_analysis(session, frame=frame, decision=decision, workflow_plan=workflow, action_plan=plan)
        records = await preflight_service.run_plan(session, action_plan_id=plan.id)
        assert records[0].status == "action_required"
        # Check EC-1: Check correlation with human_action_id
        check_item = records[0].checks_json[0]
        assert check_item.get("key") == "target_server_check"
        assert check_item.get("human_action_id") is not None

        # Verify HumanActionRequestRecord created in DB
        action_req = await session.scalar(select(HumanActionRequestRecord).where(HumanActionRequestRecord.action_plan_id == plan.id))
        assert action_req is not None
        assert action_req.state == "pending"
        assert action_req.target_param == "target_server"

    # Resolve action via saga
    async with session_factory() as session, session.begin():
        corrected = await resolution_service.resolve(
            session,
            ticket_id=plan.task_id,
            human_action_id=action_req.id,
            candidate_id="srv_1",
            operator="admin_tester",
            snapshot_hash=plan.snapshot_hash,
            plan_hash=plan.plan_hash,
        )
        assert corrected.actions[0].params["target_server"] == "srv-01.corp"

        # Verify action request state changed to resolved
        resolved_req = await session.scalar(select(HumanActionRequestRecord).where(HumanActionRequestRecord.id == action_req.id))
        assert resolved_req.state == "resolved"
        assert resolved_req.selected_candidate_id == "srv_1"

    # EC-3: Duplicate resolve attempt raises conflict
    async with session_factory() as session, session.begin():
        with pytest.raises(HumanActionConflictError, match="human_action_already_resolved"):
            await resolution_service.resolve(
                session,
                ticket_id=plan.task_id,
                human_action_id=action_req.id,
                candidate_id="srv_1",
                operator="admin_tester",
            )


async def test_human_action_resolution_rejected_by_executor(session_factory) -> None:
    from core.automation.contracts import ActionProposal, compute_action_plan_hash
    from core.automation.resolution import HumanActionResolutionService, HumanActionValidationError
    from core.database.models import HumanActionRequestRecord

    frame, decision, workflow, base_plan = _printer_plan()
    action = ActionProposal(
        id="action_interactive_0",
        sequence_no=1,
        capability_key="interactive_op",
        params={},
    )
    draft = base_plan.model_copy(update={"actions": [action]})
    plan = draft.model_copy(update={"plan_hash": compute_action_plan_hash(draft)})

    registry = CapabilityRegistry(
        (
            CapabilitySpec(
                key="interactive_op",
                version="1.0.0",
                name="Interactive Operation",
                description="test",
                executor="test",
                risk=CapabilityRisk.medium,
                idempotency=IdempotencyMode.key_guarded,
                is_mutating=True,
            ),
        )
    )
    executor = _InteractiveExecutor(reject_selection=True)
    registry.bind_executor("interactive_op", executor)
    repository = AutomationRepository()
    preflight_service = ActionPreflightService(registry)
    resolution_service = HumanActionResolutionService(registry, repository, preflight_service)

    async with session_factory() as session, session.begin():
        await repository.save_analysis(session, frame=frame, decision=decision, workflow_plan=workflow, action_plan=plan)
        await preflight_service.run_plan(session, action_plan_id=plan.id)
        action_req = await session.scalar(select(HumanActionRequestRecord).where(HumanActionRequestRecord.action_plan_id == plan.id))
        assert action_req is not None

    async with session_factory() as session, session.begin():
        with pytest.raises(HumanActionValidationError, match="candidate_validation_failed_by_executor"):
            await resolution_service.resolve(
                session,
                ticket_id=plan.task_id,
                human_action_id=action_req.id,
                candidate_id="srv_1",
                operator="admin_tester",
            )

