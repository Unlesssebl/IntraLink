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
from core.automation.runner import WorkflowRunner
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
            ),
        )
    )
    registry.bind_executor("add_wlan_group_member", _Executor())
    return registry


async def test_dispatcher_executes_only_bound_preflighted_capability(
    session_factory, capability_registry
) -> None:
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
    workflow, plan = WorkflowCompiler(
        get_default_workflow_registry(), capability_registry
    ).compile(frame=frame, decision=decision)
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
    async with session_factory() as session, session.begin():
        await repository.approve_action_plan(
            session,
            action_plan_id=plan.id,
            expected_plan_hash=plan.plan_hash,
            expected_snapshot_hash=plan.snapshot_hash,
            operator_username="operator",
        )
        advanced = await WorkflowRunner().advance(session, action_plan_id=plan.id, initiator="operator")
        assert advanced.command is not None
        command_id = advanced.command.id

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
    assert client.update_task.await_args.kwargs["status_id"] == 3
    async with session_factory() as session:
        command = await session.scalar(select(CommandRecord).where(CommandRecord.id == command_id))
        feedback = await session.scalar(
            select(ExecutionFeedbackRecord).where(ExecutionFeedbackRecord.command_id == command_id)
        )
        assert command is not None and command.status == "succeeded"
        assert feedback is not None and feedback.outcome == "succeeded"
