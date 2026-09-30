"""ADR 0006 capability-only dispatcher integration tests."""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

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
)
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import CaseCandidate, CaseDecision, CaseDecisionState, CaseFrame
from core.automation.persistence import AutomationRepository
from core.automation.preflight import ActionPreflightService
from core.automation.snapshot import TicketSnapshotFactory
from core.automation.workflows import get_default_workflow_registry
from core.database.base import Base
from core.database.models import CommandRecord, ExecutionFeedbackRecord
from core.intraservice.auth import ServiceAuthCredentials
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from worker.src.tasks.command_dispatcher import (
    dispatch_command_task,
    set_dispatcher_capability_registry,
    set_dispatcher_client,
    set_dispatcher_redis_client,
    set_dispatcher_service_auth,
    set_session_factory,
)


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, count: int, key: str, token: str, *args):
        if self.values.get(key) != token:
            return 0
        if "del" in script:
            self.values.pop(key, None)
        return 1


class _Executor:
    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        return CapabilityPreflight(status=PreflightStatus.passed, checks=["ldap_identity"])

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        return CapabilityExecution(
            outcome=CapabilityOutcome.succeeded,
            proof={"sam_account_name": params["target_user"], "membership_verified": True},
        )


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)
    yield factory
    set_session_factory(None)
    await engine.dispose()


@pytest.fixture
def capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry(
        (
            CapabilitySpec(
                key="add_wlan_group_member",
                version="test",
                name="WLAN",
                description="test",
                executor="ldap",
                required_params=("target_user",),
                risk=CapabilityRisk.medium,
                idempotency=IdempotencyMode.naturally_idempotent,
                is_mutating=True,
                requires_service_binding=True,
                auto_execution_eligible=True,
            ),
        )
    )
    registry.bind_executor("add_wlan_group_member", _Executor())
    return registry


async def test_dispatcher_executes_only_bound_preflighted_capability(
    session_factory, capability_registry, monkeypatch
) -> None:
    from core.automation.authorization import PlanAuthorizationService
    from core.automation.execution_policy import ExecutionPolicyDecision, ExecutionPolicyEvaluator
    from core.automation.service_routing import (
        ServiceCompatibilityDecision,
        ServiceCompatibilityState,
        ServiceRouteBinding,
    )

    monkeypatch.setattr(
        ExecutionPolicyEvaluator,
        "evaluate_for_action",
        AsyncMock(
            return_value=ExecutionPolicyDecision(
                allowed=True,
                phase="execution",
                capability_key="add_wlan_group_member",
                policy_revision=1,
                workflow_policy_revision=1,
                engine_version="test",
                context_hash="test",
                context_json={"test": True},
                decision_hash="test-decision-hash",
                reasons=[],
            )
        ),
    )
    task = TaskDTO(
        Id=88001,
        Name="Предоставить WLAN",
        Description="Нужен доступ к корпоративному Wi-Fi",
        ServiceId=63,
        ServiceName="Wi-Fi",
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
        entities=ExtractedEntitiesDTO(target_user="petrov.p"),
    )
    snapshot = TicketSnapshotFactory.create(task)
    frame = CaseFrame(
        task_id=task.id,
        snapshot_hash=snapshot.snapshot_hash,
        frame_version="case-frame-v1",
        entities={"target_user": "petrov.p"},
    )
    decision = CaseDecision(
        task_id=task.id,
        snapshot_hash=snapshot.snapshot_hash,
        frame_id=frame.id,
        router_version="case-router-v1",
        state=CaseDecisionState.selected,
        primary_case_type="wireless_access_request",
        candidates=[CaseCandidate(case_type="wireless_access_request", case_type_version="1.0.0")],
    )
    cat_hash = "c" * 64
    compatibility = ServiceCompatibilityDecision(
        task_id=task.id,
        snapshot_hash=snapshot.snapshot_hash,
        case_decision_id=decision.id,
        source_service_id=63,
        catalog_hash=cat_hash,
        state=ServiceCompatibilityState.compatible,
    )
    binding = ServiceRouteBinding(
        key="wifi_access",
        version="1.0.0",
        catalog_hash=cat_hash,
        service_ids=(63,),
        allowed_case_types=("wireless_access_request",),
        allowed_workflows=("wireless_access_workflow",),
        allowed_capabilities=("add_wlan_group_member",),
        is_active=True,
        is_validated=True,
    )
    workflow, plan = WorkflowCompiler(
        get_default_workflow_registry(), capability_registry
    ).compile(frame=frame, decision=decision, compatibility=compatibility, binding=binding)
    assert plan is not None

    repository = AutomationRepository()
    async with session_factory() as session, session.begin():
        await repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=plan,
        )
        await ActionPreflightService(capability_registry).run_plan(session, action_plan_id=plan.id)

    auth_service = PlanAuthorizationService(capabilities=capability_registry)
    async with session_factory() as session, session.begin():
        auth_res = await auth_service.authorize(
            session=session,
            action_plan_id=plan.id,
            expected_plan_hash=plan.plan_hash,
            expected_snapshot_hash=plan.snapshot_hash,
            kind="operator",
            actor="operator",
        )
        assert auth_res.allowed is True
        assert auth_res.command is not None
        command_id = auth_res.command.id

    client = AsyncMock()
    client.get_task.return_value = task
    client.get_task_lifetime.return_value = []
    auth = AsyncMock()
    auth.bootstrap_auth.return_value = ServiceAuthCredentials(auth_b64="dGVzdA==", bot_user_id=999, login="bot")
    set_dispatcher_client(client)
    set_dispatcher_service_auth(auth)
    set_dispatcher_redis_client(_Redis())
    set_dispatcher_capability_registry(capability_registry)
    try:
        result = await dispatch_command_task(command_id)
    finally:
        set_dispatcher_client(None)
        set_dispatcher_service_auth(None)
        set_dispatcher_redis_client(None)
        set_dispatcher_capability_registry(None)

    assert result["status"] == "succeeded"
    client.update_task.assert_awaited_once()
    assert client.update_task.await_args.kwargs["status_id"] in (3, 29)
    async with session_factory() as session:
        command = await session.scalar(select(CommandRecord).where(CommandRecord.id == command_id))
        feedback = await session.scalar(
            select(ExecutionFeedbackRecord).where(ExecutionFeedbackRecord.command_id == command_id)
        )
        assert command is not None and command.status == "succeeded"
        assert feedback is not None and feedback.outcome == "succeeded"


async def test_dispatcher_ad_gate_denial_stops_execution_without_mutation(
    session_factory, monkeypatch
) -> None:
    from core.automation.authorization import PlanAuthorizationService
    from core.automation.capabilities import (
        CapabilityExecution,
        CapabilityOutcome,
        CapabilityPreflight,
        CapabilityRegistry,
        CapabilityRisk,
        CapabilitySpec,
        IdempotencyMode,
        PreflightStatus,
    )
    from core.automation.compiler import WorkflowCompiler
    from core.automation.contracts import (
        CaseCandidate,
        CaseDecision,
        CaseDecisionState,
        CaseFrame,
    )
    from core.automation.persistence import AutomationRepository
    from core.automation.preflight import ActionPreflightService
    from core.automation.service_routing import (
        ServiceCompatibilityDecision,
        ServiceCompatibilityState,
        ServiceRouteBinding,
    )
    from core.automation.workflows import get_default_workflow_registry
    from core.database.models import (
        ActionPlanRecord,
        CommandRecord,
        ExecutionFeedbackRecord,
        ExecutionPolicyDecisionRecord,
        WorkflowPlanRecord,
    )

    mock_ad_executor = AsyncMock()
    mock_ad_executor.preflight = AsyncMock(
        return_value=CapabilityPreflight(status=PreflightStatus.passed, checks=["ou_writable"])
    )
    mock_ad_executor.execute = AsyncMock(
        return_value=CapabilityExecution(outcome=CapabilityOutcome.succeeded, proof={})
    )

    from core.automation.execution_policy import ExecutionPolicyDecision, ExecutionPolicyEvaluator

    async def mock_evaluate_for_action(*args, **kwargs):
        phase = kwargs.get("phase")
        ext = kwargs.get("external_guard_reasons", ())
        if phase == "authorization":
            return ExecutionPolicyDecision(
                allowed=True,
                phase="authorization",
                capability_key="create_ad_user",
                policy_revision=1,
                workflow_policy_revision=1,
                engine_version="test",
                context_hash="auth-ctx",
                decision_hash="auth-dec-hash",
                reasons=[],
                context_json={},
            )
        allowed = len(ext) == 0
        return ExecutionPolicyDecision(
            allowed=allowed,
            phase="execution",
            capability_key="create_ad_user",
            policy_revision=1,
            workflow_policy_revision=1,
            engine_version="test",
            context_hash="exec-ctx",
            decision_hash="exec-dec-hash",
            reasons=list(ext),
            context_json={},
        )

    monkeypatch.setattr(ExecutionPolicyEvaluator, "evaluate_for_action", mock_evaluate_for_action)

    ad_registry = CapabilityRegistry(
        (
            CapabilitySpec(
                key="create_ad_user",
                version="1.0.0",
                name="Create AD User",
                description="Create AD user account",
                executor="ldap",
                required_params=("first_name", "last_name", "department", "title"),
                risk=CapabilityRisk.high,
                idempotency=IdempotencyMode.key_guarded,
                is_mutating=True,
                requires_service_binding=True,
                auto_execution_eligible=False,
            ),
        )
    )
    ad_registry.bind_executor("create_ad_user", mock_ad_executor)

    task = TaskDTO(
        Id=99001,
        Name="Создать учетную запись",
        Description="Выход сотрудника",
        ServiceId=40,
        ServiceName="Onboarding",
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
        entities=ExtractedEntitiesDTO(
            first_name="Иван",
            last_name="Иванов",
            middle_name="Иванович",
            department="ИТ",
            title="Инженер",
        ),
    )
    snapshot = TicketSnapshotFactory.create(task)
    frame = CaseFrame(
        task_id=task.id,
        snapshot_hash=snapshot.snapshot_hash,
        frame_version="case-frame-v1",
        entities={
            "first_name": "Иван",
            "last_name": "Иванов",
            "middle_name": "Иванович",
            "department": "ИТ",
            "title": "Инженер",
        },
    )
    decision = CaseDecision(
        task_id=task.id,
        snapshot_hash=snapshot.snapshot_hash,
        frame_id=frame.id,
        router_version="case-router-v1",
        state=CaseDecisionState.selected,
        primary_case_type="employee_onboarding",
        candidates=[CaseCandidate(case_type="employee_onboarding", case_type_version="1.0.0")],
    )
    cat_hash = "d" * 64
    compatibility = ServiceCompatibilityDecision(
        task_id=task.id,
        snapshot_hash=snapshot.snapshot_hash,
        case_decision_id=decision.id,
        source_service_id=40,
        catalog_hash=cat_hash,
        state=ServiceCompatibilityState.compatible,
    )
    binding = ServiceRouteBinding(
        key="ad_account_creation",
        version="1.0.0",
        catalog_hash=cat_hash,
        service_ids=(40,),
        allowed_case_types=("employee_onboarding",),
        allowed_workflows=("employee_onboarding_workflow",),
        allowed_capabilities=("create_ad_user",),
        is_active=True,
        is_validated=True,
    )
    workflow, plan = WorkflowCompiler(
        get_default_workflow_registry(), ad_registry
    ).compile(frame=frame, decision=decision, compatibility=compatibility, binding=binding)
    assert plan is not None

    repository = AutomationRepository()
    async with session_factory() as session, session.begin():
        await repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=plan,
        )
        await ActionPreflightService(ad_registry).run_plan(session, action_plan_id=plan.id)

    auth_service = PlanAuthorizationService(capabilities=ad_registry)
    async with session_factory() as session, session.begin():
        auth_res = await auth_service.authorize(
            session=session,
            action_plan_id=plan.id,
            expected_plan_hash=plan.plan_hash,
            expected_snapshot_hash=plan.snapshot_hash,
            kind="operator",
            actor="admin",
        )
        assert auth_res.allowed is True
        assert auth_res.command is not None
        command_id = auth_res.command.id

    client = AsyncMock()
    # Task is identical (snapshot fresh), but database has no validated catalog -> AD gate denies
    client.get_task.return_value = task
    client.get_task_lifetime.return_value = []
    auth = AsyncMock()
    auth.bootstrap_auth.return_value = ServiceAuthCredentials(auth_b64="dGVzdA==", bot_user_id=999, login="bot")
    set_dispatcher_client(client)
    set_dispatcher_service_auth(auth)
    set_dispatcher_redis_client(_Redis())
    set_dispatcher_capability_registry(ad_registry)
    try:
        result = await dispatch_command_task(command_id)
    finally:
        set_dispatcher_client(None)
        set_dispatcher_service_auth(None)
        set_dispatcher_redis_client(None)
        set_dispatcher_capability_registry(None)

    assert result["status"] == "failed"
    assert "service_catalog_changed" in result["error"]
    # Verify 0 executor calls occurred
    mock_ad_executor.execute.assert_not_awaited()

    async with session_factory() as session:
        command = await session.scalar(select(CommandRecord).where(CommandRecord.id == command_id))
        feedback = await session.scalar(
            select(ExecutionFeedbackRecord).where(ExecutionFeedbackRecord.command_id == command_id)
        )
        plan_rec = await session.scalar(select(ActionPlanRecord).where(ActionPlanRecord.id == plan.id))
        wf_rec = await session.scalar(select(WorkflowPlanRecord).where(WorkflowPlanRecord.id == workflow.id))
        decision_rec = await session.scalar(
            select(ExecutionPolicyDecisionRecord)
            .where(ExecutionPolicyDecisionRecord.command_id == command_id, ExecutionPolicyDecisionRecord.phase == "execution")
        )

        assert command is not None and command.status == "failed"
        assert feedback is not None and feedback.outcome == "denied"
        assert "service_catalog_changed" in feedback.result_json["reasons"]
        assert plan_rec is not None and plan_rec.state == "needs_review"
        assert wf_rec is not None and wf_rec.state == "needs_review"
        assert decision_rec is not None and decision_rec.allowed is False
        assert "service_catalog_changed" in decision_rec.reasons_json
