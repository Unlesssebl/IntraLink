"""ADR 0006 API service vertical slice."""

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.features.autopilot.schemas import (
    ApproveActionPlanRequest,
    CorrectActionPlanRequest,
    CorrectCaseDecisionRequest,
    CorrectedActionRequest,
)
from api.src.features.autopilot.service import AutomationService
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
from core.automation.case_router import CaseRouter
from core.automation.frame_extractor import CaseFrameExtractor
from core.database.base import Base
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO


class _Executor:
    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        return CapabilityPreflight(status=PreflightStatus.passed)

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        return CapabilityExecution(outcome=CapabilityOutcome.succeeded)


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _capabilities() -> CapabilityRegistry:
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


def _task() -> TaskDTO:
    return TaskDTO(
        Id=62001,
        Name="Доступ к Wi-Fi",
        Description="Прошу предоставить доступ к WLAN-WORKNET",
        ServiceId=63,
        ServiceName="Wi-Fi",
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
        entities=ExtractedEntitiesDTO(target_user="petrov.p"),
    )


def _service(client: AsyncMock) -> AutomationService:
    return AutomationService(
        client=client,
        extractor=CaseFrameExtractor(),
        router=CaseRouter(),
        capabilities=_capabilities(),
    )


async def test_read_only_get_does_not_trigger_analysis(session_factory) -> None:
    client = AsyncMock()
    service = _service(client)
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await service.get_automation(session, 62001)
    assert exc.value.status_code == 404
    client.get_task.assert_not_awaited()


async def test_analyze_then_approve_creates_bound_first_command(session_factory) -> None:
    client = AsyncMock()
    client.get_task.return_value = _task()
    client.get_task_lifetime.return_value = []
    service = _service(client)
    async with session_factory() as session:
        automation = await service.analyze(
            session,
            ticket_id=62001,
            auth_b64="dGVzdDp0ZXN0",
            force=False,
        )
    assert automation.case_decision.primary_case_type == "wireless_access_request"
    assert automation.action_plan is not None
    assert automation.action_plan.actions[0].capability_key == "add_wlan_group_member"

    request = ApproveActionPlanRequest(
        action_plan_id=automation.action_plan.id,
        plan_hash=automation.action_plan.plan_hash,
        snapshot_hash=automation.snapshot_hash,
    )
    with patch("api.src.features.autopilot.service.dispatch_command", new_callable=AsyncMock) as dispatch:
        async with session_factory() as session:
            response = await service.approve(
                session,
                ticket_id=62001,
                request=request,
                operator="operator",
                auth_b64="dGVzdDp0ZXN0",
            )
    assert response.status == "approved"
    assert response.command_id is not None
    dispatch.assert_awaited_once_with(response.command_id)


async def test_operator_can_replace_action_plan_before_approval(session_factory) -> None:
    client = AsyncMock()
    client.get_task.return_value = _task()
    client.get_task_lifetime.return_value = []
    service = _service(client)
    async with session_factory() as session:
        automation = await service.analyze(
            session,
            ticket_id=62001,
            auth_b64="dGVzdDp0ZXN0",
            force=False,
        )
    assert automation.action_plan is not None
    original_id = automation.action_plan.id
    request = CorrectActionPlanRequest(
        action_plan_id=original_id,
        plan_hash=automation.action_plan.plan_hash,
        snapshot_hash=automation.snapshot_hash,
        actions=[
            CorrectedActionRequest(
                capability_key="add_wlan_group_member",
                params={"target_user": "ivanov.i"},
            )
        ],
        reason_tag="operator_corrected_target",
    )
    async with session_factory() as session:
        corrected = await service.correct_action_plan(
            session,
            ticket_id=62001,
            request=request,
            operator="operator",
            auth_b64="dGVzdDp0ZXN0",
        )
    assert corrected.action_plan is not None
    assert corrected.action_plan.id != original_id
    assert corrected.action_plan.actions[0].params == {"target_user": "ivanov.i"}
    assert corrected.action_plan.state == "ready"


async def test_action_correction_rejects_capability_outside_workflow(session_factory) -> None:
    client = AsyncMock()
    client.get_task.return_value = _task()
    client.get_task_lifetime.return_value = []
    service = _service(client)
    async with session_factory() as session:
        automation = await service.analyze(
            session,
            ticket_id=62001,
            auth_b64="dGVzdDp0ZXN0",
            force=False,
        )
    assert automation.action_plan is not None
    request = CorrectActionPlanRequest(
        action_plan_id=automation.action_plan.id,
        plan_hash=automation.action_plan.plan_hash,
        snapshot_hash=automation.snapshot_hash,
        actions=[CorrectedActionRequest(capability_key="disable_ad_user", params={"target_user": "ivanov.i"})],
        reason_tag="invalid_capability",
    )
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await service.correct_action_plan(
                session,
                ticket_id=62001,
                request=request,
                operator="operator",
                auth_b64="dGVzdDp0ZXN0",
            )
    assert exc.value.status_code == 422
    assert exc.value.detail == "capability_not_allowed_by_workflow"


async def test_case_correction_cancels_the_previous_action_plan(session_factory) -> None:
    client = AsyncMock()
    client.get_task.return_value = _task()
    client.get_task_lifetime.return_value = []
    service = _service(client)
    async with session_factory() as session:
        automation = await service.analyze(
            session,
            ticket_id=62001,
            auth_b64="dGVzdDp0ZXN0",
            force=False,
        )
    assert automation.action_plan is not None
    old_plan = automation.action_plan
    correction = CorrectCaseDecisionRequest(
        case_decision_id=automation.case_decision.id,
        snapshot_hash=automation.snapshot_hash,
        corrected_case_type="unknown",
        reason_tag="operator_cannot_classify",
    )
    async with session_factory() as session:
        corrected = await service.correct_case(
            session,
            ticket_id=62001,
            request=correction,
            operator="operator",
            auth_b64="dGVzdDp0ZXN0",
        )
    assert corrected.case_decision.state == "unknown"
    assert corrected.action_plan is None

    old_approval = ApproveActionPlanRequest(
        action_plan_id=old_plan.id,
        plan_hash=old_plan.plan_hash,
        snapshot_hash=old_plan.snapshot_hash,
    )
    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await service.approve(
                session,
                ticket_id=62001,
                request=old_approval,
                operator="operator",
                auth_b64="dGVzdDp0ZXN0",
            )
    assert exc.value.status_code == 409
    assert exc.value.detail == "action_plan_not_ready"
