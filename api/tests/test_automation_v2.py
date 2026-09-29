"""ADR 0006 API service vertical slice."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.features.autopilot.schemas import (
    ApproveActionPlanRequest,
    CorrectActionPlanRequest,
    CorrectCaseDecisionRequest,
    CorrectedActionRequest,
    CorrectTargetServiceRequest,
    RedirectPlanRequest,
    RetryClarificationRequest,
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
from core.database.models import (
    ServiceCatalogEntryRecord,
    ServiceCatalogVersionRecord,
    ServiceRouteBindingRecord,
    ServiceRoutingFeedbackRecord,
)
from core.intraservice.auth import ServiceAuthCredentials, ServiceAuthError
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO


class _Executor:
    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        return CapabilityPreflight(status=PreflightStatus.passed)

    async def execute(self, params: dict[str, Any], *, context: CapabilityExecutionContext) -> CapabilityExecution:
        return CapabilityExecution(outcome=CapabilityOutcome.succeeded)


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog_hash = "a" * 64
    async with factory() as session:
        version = ServiceCatalogVersionRecord(
            version=1,
            catalog_hash=catalog_hash,
            fetched_at=datetime.now(UTC),
            source="test",
            validation_state="validated",
            is_active=True,
        )
        session.add(version)
        await session.flush()
        session.add(
            ServiceCatalogEntryRecord(
                catalog_version_id=version.id,
                service_id=63,
                service_path="WLAN → Доступ",
                is_active=True,
                form_metadata_json={},
                field_metadata_json=[],
                catalog_hash=catalog_hash,
            )
        )
        session.add(
            ServiceCatalogEntryRecord(
                catalog_version_id=version.id,
                service_id=55,
                service_path="05. Directum → Пользователь Directum",
                task_type_id=1018,
                is_active=True,
                form_metadata_json={},
                field_metadata_json=[],
                catalog_hash=catalog_hash,
            )
        )
        session.add(
            ServiceCatalogEntryRecord(
                catalog_version_id=version.id,
                service_id=900001,
                service_path="01. Учётные записи → Создание пользователя сети",
                task_type_id=1001,
                is_active=True,
                form_metadata_json={},
                field_metadata_json=[],
                catalog_hash=catalog_hash,
            )
        )
        session.add(
            ServiceRouteBindingRecord(
                key="wlan_access",
                version="test",
                service_ids_json=[63],
                allowed_case_types_json=["wireless_access_request"],
                default_case_type="wireless_access_request",
                allowed_workflows_json=["wireless_access_workflow"],
                allowed_capabilities_json=["add_wlan_group_member"],
                required_fields_json=[],
                redirect_strategy="cancel_and_recreate",
                risk="medium",
                is_active=True,
                is_validated=True,
                catalog_hash=catalog_hash,
            )
        )
        session.add(
            ServiceRouteBindingRecord(
                key="directum_user",
                version="test",
                service_ids_json=[55],
                allowed_case_types_json=["knowledge_request"],
                default_case_type="knowledge_request",
                allowed_workflows_json=["knowledge_consultation_workflow"],
                allowed_capabilities_json=[],
                required_task_type_id=1018,
                required_fields_json=[],
                redirect_strategy="manual",
                risk="medium",
                is_active=True,
                is_validated=True,
                catalog_hash=catalog_hash,
            )
        )
        session.add(
            ServiceRouteBindingRecord(
                key="ad_account_creation",
                version="test",
                service_ids_json=[900001],
                allowed_case_types_json=["employee_onboarding"],
                default_case_type="employee_onboarding",
                allowed_workflows_json=["employee_onboarding_workflow"],
                allowed_capabilities_json=["create_ad_user"],
                required_task_type_id=1001,
                required_fields_json=[],
                redirect_strategy="cancel_and_recreate",
                risk="high",
                is_active=True,
                is_validated=True,
                catalog_hash=catalog_hash,
            )
        )
        await session.commit()
    yield factory
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
        result = await service.get_automation(session, 62001)
    assert result is None
    client.get_task.assert_not_awaited()


async def test_onboarding_reanalysis_does_not_duplicate_clarification(session_factory) -> None:
    client = AsyncMock()
    client.get_task.return_value = TaskDTO(
        Id=63001,
        Name="Создать учетную запись нового сотрудника",
        Description="Нужно создать пользователя",
        ServiceId=900001,
        ServiceName="Создание пользователя сети",
        TaskTypeId=1001,
        StatusId=2,
        entities=ExtractedEntitiesDTO(last_name="Иванов", first_name="Иван"),
    )
    client.get_task_lifetime.return_value = []
    service = AutomationService(client=client, extractor=CaseFrameExtractor(), router=CaseRouter())
    service.auth_bootstrap = AsyncMock()
    service.auth_bootstrap.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="service-auth",
        bot_user_id=999,
        login="bot",
    )

    async with session_factory() as session:
        first = await service.analyze(session, ticket_id=63001, auth_b64="operator-auth", force=True)
        second = await service.analyze(session, ticket_id=63001, auth_b64="operator-auth", force=True)

    assert first.workflow_plan.state.value == "awaiting_facts"
    assert second.workflow_plan.active_clarification_id == first.workflow_plan.active_clarification_id
    assert client.update_task.await_count == 1
    assert "status_id" not in client.update_task.await_args.kwargs
    assert client.update_task.await_args.kwargs["comment"]
    assert first.readiness is not None
    assert first.readiness.preflight.state == "not_started"
    assert first.readiness.preflight.reason_code == "blocked_by_missing_facts"
    assert {item.code for item in first.readiness.blockers} == {"required_facts_missing"}


async def test_onboarding_extracts_missing_facts_after_service_routing(session_factory) -> None:
    client = AsyncMock()
    client.get_task.return_value = TaskDTO(
        Id=63002,
        Name="Создать учетную запись нового сотрудника",
        Description=(
            "Фамилия: Иванов\nИмя: Иван\n"
            "Подразделение: Бюро разработки маршрутов изготовления МК\n"
            "Должность: Инженер"
        ),
        ServiceId=900001,
        ServiceName="Создание пользователя сети",
        TaskTypeId=1001,
        StatusId=2,
    )
    client.get_task_lifetime.return_value = []
    service = AutomationService(client=client, extractor=CaseFrameExtractor(), router=CaseRouter())

    async with session_factory() as session:
        result = await service.analyze(session, ticket_id=63002, auth_b64="operator-auth", force=True)

    assert result.case_decision.primary_case_type == "employee_onboarding"
    assert result.case_frame.entities["last_name"] == "Иванов"
    assert result.case_frame.entities["department"] == "Бюро разработки маршрутов изготовления МК"
    assert result.workflow_plan.missing_facts == []
    assert result.action_plan is not None
    assert result.action_plan.actions[0].capability_key == "create_ad_user"
    assert "target_user" not in result.action_plan.actions[0].params
    client.update_task.assert_not_awaited()


async def test_failed_clarification_is_visible_and_retry_is_idempotent(session_factory) -> None:
    client = AsyncMock()
    client.get_task.return_value = TaskDTO(
        Id=63003,
        Name="Создать учетную запись нового сотрудника",
        Description="Нужно создать пользователя",
        ServiceId=900001,
        ServiceName="Создание пользователя сети",
        TaskTypeId=1001,
        StatusId=2,
    )
    client.get_task_lifetime.return_value = []
    client.update_task.side_effect = [RuntimeError("HTTP 400 with secret token=hidden"), None]
    service = AutomationService(client=client, extractor=CaseFrameExtractor(), router=CaseRouter())
    service.auth_bootstrap = AsyncMock()
    service.auth_bootstrap.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="service-auth", bot_user_id=999, login="bot"
    )

    async with session_factory() as session:
        failed = await service.analyze(session, ticket_id=63003, auth_b64="operator-auth", force=True)
        assert failed.readiness is not None
        clarification = failed.readiness.clarification
        assert clarification is not None and clarification.state == "failed"
        assert clarification.publish_attempts == 1
        assert "secret" not in (clarification.last_error_detail or "").lower()
        retried = await service.retry_clarification(
            session,
            ticket_id=63003,
            clarification_id=clarification.id,
            request=RetryClarificationRequest(snapshot_hash=failed.snapshot_hash),
            auth_b64="operator-auth",
        )

    assert retried.readiness is not None
    assert retried.readiness.clarification is not None
    assert retried.readiness.clarification.state == "published"
    assert retried.readiness.clarification.publish_attempts == 2
    assert retried.workflow_plan.state.value == "awaiting_facts"
    assert client.update_task.await_count == 2


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


async def test_redirect_plan_approval_comments_before_confirmed_cancel(session_factory) -> None:
    wrong_service = TaskDTO(
        Id=62002,
        Name="Новый сотрудник",
        Description="Создать доменную учетную запись новому сотруднику",
        ServiceId=55,
        ServiceName="Пользователь Directum",
        TaskTypeId=1018,
        StatusId=2,
        StatusName="В работе",
        ExecutorIds="999",
    )
    cancelled = wrong_service.model_copy(update={"status_id": 30, "status_name": "Отменена"})
    client = AsyncMock()
    client.get_task.return_value = wrong_service
    client.get_task_lifetime.return_value = []
    service = _service(client)
    async with session_factory() as session:
        automation = await service.analyze(session, ticket_id=62002, auth_b64="dGVzdDp0ZXN0", force=False)
    assert automation.service_compatibility.state == "mismatch"
    assert automation.action_plan is None
    assert automation.redirect_plan is not None
    assert automation.redirect_plan.target_service_id == 900001

    client.get_task.side_effect = [wrong_service, cancelled]
    client.add_task_comment.return_value = {"ok": True}
    client.update_task.return_value = {"ok": True}
    request = RedirectPlanRequest(
        redirect_plan_id=automation.redirect_plan.id,
        plan_hash=automation.redirect_plan.plan_hash,
        snapshot_hash=automation.snapshot_hash,
        version=automation.redirect_plan.version,
    )
    async with session_factory() as session:
        result = await service.approve_redirect(
            session,
            ticket_id=62002,
            request=request,
            operator="operator",
            auth_b64="dGVzdDp0ZXN0",
        )
        assert result.status == "succeeded"
    client.add_task_comment.assert_awaited_once()
    client.update_task.assert_awaited_once_with(task_id=62002, status_id=30, auth_b64="dGVzdDp0ZXN0")


async def test_operator_can_correct_target_service_without_granting_execution(session_factory) -> None:
    wrong_service = TaskDTO(
        Id=62004,
        Name="Новый сотрудник",
        Description="Создать доменную учетную запись новому сотруднику",
        ServiceId=55,
        ServiceName="Пользователь Directum",
        TaskTypeId=1018,
        StatusId=2,
        StatusName="В работе",
    )
    client = AsyncMock()
    client.get_task.return_value = wrong_service
    client.get_task_lifetime.return_value = []
    service = _service(client)
    async with session_factory() as session:
        initial = await service.analyze(session, ticket_id=62004, auth_b64="dGVzdDp0ZXN0", force=False)
        corrected = await service.correct_target_service(
            session,
            ticket_id=62004,
            request=CorrectTargetServiceRequest(
                compatibility_decision_id=initial.service_compatibility.id,
                snapshot_hash=initial.snapshot_hash,
                target_service_id=900001,
            ),
            operator="operator",
            auth_b64="dGVzdDp0ZXN0",
        )
        feedback = await session.scalar(
            select(ServiceRoutingFeedbackRecord).where(ServiceRoutingFeedbackRecord.task_id == 62004)
        )

    assert corrected.service_compatibility.target_service_id == 900001
    assert corrected.service_compatibility.target_selection_method == "operator_confirmation"
    assert corrected.service_compatibility.authorization_state == "not_authorized"
    assert feedback is not None
    assert feedback.selected_target_service_id == 900001


@pytest.mark.asyncio
async def test_auth_token_restores_saved_credentials_from_shared_vault() -> None:
    bootstrap = AsyncMock()
    bootstrap.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="c2VydmljZS5ib3Q6c2VjcmV0",
        bot_user_id=42,
        login="service.bot",
    )
    service = AutomationService(client=AsyncMock(), auth_bootstrap=bootstrap)
    redis = AsyncMock()

    with patch("api.src.features.autopilot.service.get_redis_client", return_value=redis):
        token = await service._auth_token(None)

    assert token == "c2VydmljZS5ib3Q6c2VjcmV0"
    bootstrap.bootstrap_auth.assert_awaited_once()
    call = bootstrap.bootstrap_auth.await_args
    assert call.kwargs["client"] is service.client
    assert call.kwargs["redis_client"] is redis
    assert call.kwargs["session_factory"] is not None


@pytest.mark.asyncio
async def test_auth_token_returns_controlled_503_when_vault_is_empty() -> None:
    bootstrap = AsyncMock()
    bootstrap.bootstrap_auth.side_effect = ServiceAuthError("missing")
    service = AutomationService(client=AsyncMock(), auth_bootstrap=bootstrap)

    with patch("api.src.features.autopilot.service.get_redis_client", return_value=AsyncMock()):
        with pytest.raises(HTTPException) as exc_info:
            await service._auth_token(None)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "service_credentials_unavailable"


async def test_redirect_partial_outcome_is_not_republished(session_factory) -> None:
    wrong_service = TaskDTO(
        Id=62003,
        Name="Новый сотрудник",
        Description="Создать доменную учетную запись новому сотруднику",
        ServiceId=55,
        ServiceName="Пользователь Directum",
        TaskTypeId=1018,
        StatusId=2,
        StatusName="В работе",
    )
    client = AsyncMock()
    client.get_task.return_value = wrong_service
    client.get_task_lifetime.return_value = []
    service = _service(client)
    async with session_factory() as session:
        automation = await service.analyze(session, ticket_id=62003, auth_b64="dGVzdDp0ZXN0", force=False)
    assert automation.redirect_plan is not None
    request = RedirectPlanRequest(
        redirect_plan_id=automation.redirect_plan.id,
        plan_hash=automation.redirect_plan.plan_hash,
        snapshot_hash=automation.snapshot_hash,
        version=automation.redirect_plan.version,
    )
    client.add_task_comment.return_value = {"ok": True}
    client.update_task.side_effect = RuntimeError("status transport failed")
    async with session_factory() as session:
        result = await service.approve_redirect(
            session,
            ticket_id=62003,
            request=request,
            operator="operator",
            auth_b64="dGVzdDp0ZXN0",
        )
    assert result.status == "needs_review"
    assert result.execution_state == "partial_unknown"

    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await service.approve_redirect(
                session,
                ticket_id=62003,
                request=request,
                operator="operator",
                auth_b64="dGVzdDp0ZXN0",
            )
    assert exc.value.status_code == 409
    client.add_task_comment.assert_awaited_once()
