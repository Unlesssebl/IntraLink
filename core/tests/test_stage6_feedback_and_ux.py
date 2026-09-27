"""Comprehensive tests for Stage 6: Canonical Feedback Loop, Scenario Catalog, Quality Metrics, and Operator UX."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.features.autopilot.schemas import (
    ApprovePlanRequest,
    ManualTakeoverRequest,
    RejectPlanRequest,
)
from core.autopilot.dto import (
    AgentPlanDTO,
    PreflightCheckDTO,
    PreflightResultDTO,
)
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base
from core.database.models import (
    CommandRecord,
    PreparedPlanRecord,
    RoutingDecisionRecord,
    RoutingFeedbackRecord,
    RoutingPreflightRecord,
)
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.routing.catalog_service import ScenarioCatalogService
from core.routing.preflight import compute_canonical_params_hash


class MockRedis:
    def __init__(self) -> None:
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0, nx: bool = False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key: str):
        return int(self.store.pop(key, None) is not None)

    async def eval(self, script: str, numkeys: int, key: str, *args):
        del numkeys
        owner_token = args[0]
        if self.store.get(key) != owner_token:
            return 0
        if "expire" in script.lower():
            return 1
        return await self.delete(key)


@pytest.fixture
async def async_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture
def policy_service(async_session_factory, mock_redis) -> AutopilotPolicyService:
    return AutopilotPolicyService(session_factory=async_session_factory, redis_client=mock_redis)


@pytest.mark.asyncio
async def test_scenario_catalog_service_excludes_passwords_and_exposes_typed_fields(policy_service):
    catalog_service = ScenarioCatalogService(policy_service=policy_service)
    catalog = await catalog_service.get_catalog()

    assert len(catalog) >= 6
    scenario_keys = {item.scenario_key for item in catalog}
    assert "grant_wlan" in scenario_keys
    assert "install_printer" in scenario_keys
    assert "account_lock" in scenario_keys

    for item in catalog:
        # Passwords, Field1489, tokens must never be operator-editable
        editable_keys = [f.field_key.lower() for f in item.editable_fields]
        assert "password" not in editable_keys
        assert "user_password" not in editable_keys
        assert "field1489" not in editable_keys
        assert "token" not in editable_keys
        assert "secret" not in editable_keys

        # Ensure all fields are typed
        for field in item.editable_fields:
            assert field.field_type in ("string", "integer", "boolean", "select")
            assert field.label
            assert isinstance(field.required, bool)


def test_routing_feedback_record_sanitizes_secrets_automatically():
    feedback = RoutingFeedbackRecord(
        task_id=123,
        verdict="corrected",
        original_params={"target_user": "ivanov.i", "user_password": "super_secret_123"},
        corrected_params={"user_login": "petrov.p", "token": "secret_token_456", "Field1489": "pwd789"},
        operator_notes="Пароль исправлен оператором",
        reason_tag="wrong_params",
    )

    assert feedback.original_params["user_password"] == "***REDACTED***"
    assert feedback.corrected_params["token"] == "***REDACTED***"
    assert feedback.corrected_params["Field1489"] == "***REDACTED***"
    assert feedback.original_params["target_user"] == "ivanov.i"
    assert feedback.corrected_params["user_login"] == "petrov.p"


@pytest.mark.asyncio
async def test_quality_metrics_computation_without_pii(async_session_factory, policy_service, mock_redis):
    from api.src.features.autopilot.service import AutopilotService

    mock_client = AsyncMock()
    service = AutopilotService(
        client=mock_client,
        auth_bootstrap=AsyncMock(),
        policy_service=policy_service,
        diagnostics_service=AsyncMock(),
    )

    async with async_session_factory() as session:
        # Add sample decisions
        dec1 = RoutingDecisionRecord(
            id=uuid.uuid4(),
            task_id=101,
            snapshot_hash="h1",
            router_version="2.0.0",
            prompt_version="v1",
            state="selected",
            selected_scenario="grant_wlan",
        )
        dec2 = RoutingDecisionRecord(
            id=uuid.uuid4(),
            task_id=102,
            snapshot_hash="h2",
            router_version="2.0.0",
            prompt_version="v1",
            state="selected",
            selected_scenario="install_printer",
        )
        dec3 = RoutingDecisionRecord(
            id=uuid.uuid4(),
            task_id=103,
            snapshot_hash="h3",
            router_version="2.0.0",
            prompt_version="v1",
            state="selected",
            selected_scenario="grant_wlan",
        )
        session.add_all([dec1, dec2, dec3])

        # Add feedbacks: 1 approved, 1 corrected, 1 rejected, 1 legacy (ignored for calibration)
        fb1 = RoutingFeedbackRecord(
            decision_id=dec1.id,
            task_id=101,
            verdict="approved",
            original_scenario="grant_wlan",
            corrected_scenario="grant_wlan",
            source="runtime",
        )
        fb2 = RoutingFeedbackRecord(
            decision_id=dec2.id,
            task_id=102,
            verdict="corrected",
            original_scenario="install_printer",
            corrected_scenario="grant_wlan",
            reason_tag="wrong_scenario",
            source="runtime",
        )
        fb3 = RoutingFeedbackRecord(
            decision_id=dec3.id,
            task_id=103,
            verdict="rejected",
            original_scenario="grant_wlan",
            reason_tag="not_applicable",
            source="runtime",
        )
        fb_legacy = RoutingFeedbackRecord(
            task_id=999,
            verdict="corrected",
            original_scenario="old_sc",
            corrected_scenario="new_sc",
            source="legacy",
        )
        session.add_all([fb1, fb2, fb3, fb_legacy])
        await session.commit()

        metrics = await service.get_quality_metrics(session=session)

        assert metrics.total_decisions == 3
        assert metrics.approve_rate == pytest.approx(1 / 3, 0.01)
        assert metrics.correction_rate == pytest.approx(1 / 3, 0.01)
        assert metrics.reject_takeover_rate == pytest.approx(1 / 3, 0.01)
        assert len(metrics.scenario_transitions) >= 1
        transition = next(t for t in metrics.scenario_transitions if t["original_scenario"] == "install_printer")
        assert transition["corrected_scenario"] == "grant_wlan"
        assert transition["count"] == 1


@pytest.mark.asyncio
async def test_rejection_and_manual_takeover_do_not_create_command_records(
    async_session_factory, policy_service, mock_redis
):
    from api.src.features.autopilot.service import AutopilotService
    from core.database.models import RoutingDecisionRecord

    mock_client = AsyncMock()
    dummy_task = TaskDTO(
        id=505,
        service_id=63,
        name="Доступ Wi-Fi",
        description="Прошу доступ",
        status_id=1,
        entities=ExtractedEntitiesDTO(),
    )
    mock_client.get_task.return_value = dummy_task
    mock_client.get_task_lifetime.return_value = []

    service = AutopilotService(
        client=mock_client,
        auth_bootstrap=AsyncMock(),
        policy_service=policy_service,
        diagnostics_service=AsyncMock(),
    )

    from core.routing.snapshot import TicketSnapshotFactory
    snapshot = TicketSnapshotFactory.create(dummy_task, comments=[])

    async with async_session_factory() as session:
        decision_id = uuid.uuid4()
        plan_id = uuid.uuid4()
        snapshot_hash = snapshot.snapshot_hash

        plan_dto = AgentPlanDTO(
            task_id=505,
            decision_id=decision_id,
            plan_id=plan_id,
            scenario_key="grant_wlan",
            scenario_name="Wi-Fi",
            description="desc",
            analysis_state="ready",
            routing_state="selected",
            snapshot_hash=snapshot_hash,
            plan_hash="",
            decision_reason_codes=["exact_match"],
            proposed_action="grant_wlan",
            proposed_params={},
            suggested_comment="Доступ предоставлен",
            target_status_id=3,
            is_executable=True,
            missing_facts=[],
        )
        from core.routing.preflight import compute_canonical_plan_hash_from_json
        plan_hash = compute_canonical_plan_hash_from_json(plan_dto.model_dump(mode="json"))
        plan_dto.plan_hash = plan_hash

        dec = RoutingDecisionRecord(
            id=decision_id,
            task_id=505,
            snapshot_hash=snapshot_hash,
            router_version="2.0.0",
            state="selected",
            selected_scenario="grant_wlan",
        )
        plan = PreparedPlanRecord(
            id=plan_id,
            decision_id=decision_id,
            task_id=505,
            snapshot_hash=snapshot_hash,
            plan_hash=plan_hash,
            scenario_key="grant_wlan",
            plan_json=plan_dto.model_dump(mode="json"),
            state="ready",
        )
        session.add_all([dec, plan])
        await session.commit()

        # 1. Reject plan
        reject_req = RejectPlanRequest(
            decision_id=decision_id,
            plan_id=plan_id,
            plan_hash=plan_hash,
            snapshot_hash=snapshot_hash,
            reason_tag="not_applicable",
            operator_notes="Не требуется настройка",
        )
        reject_res = await service.reject_plan(
            ticket_id=505,
            req=reject_req,
            session=session,
            redis_client=mock_redis,
            operator_username="test_supervisor",
        )

        assert reject_res.status == "rejected"
        assert reject_res.feedback_id is not None

        # Verify feedback persisted with command_id = None
        fb = (
            await session.execute(
                select(RoutingFeedbackRecord).where(RoutingFeedbackRecord.prepared_plan_id == plan_id)
            )
        ).scalar_one()
        assert fb.verdict == "rejected"
        assert fb.command_id is None
        assert fb.reason_tag == "not_applicable"

        # Verify NO CommandRecord created
        cmds = (
            await session.execute(select(CommandRecord).where(CommandRecord.task_id == 505))
        ).scalars().all()
        assert len(cmds) == 0

        # 2. Conflicting approve after terminal rejection -> 409 Conflict
        from fastapi import HTTPException
        approve_req = ApprovePlanRequest(
            decision_id=decision_id,
            plan_id=plan_id,
            plan_hash=plan_hash,
            snapshot_hash=snapshot_hash,
            expected_status_id=1,
            last_event_id=None,
        )
        with pytest.raises(HTTPException) as exc_info:
            await service.approve_plan(
                ticket_id=505,
                req=approve_req,
                session=session,
                redis_client=mock_redis,
                operator_username="test_supervisor",
            )
        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_idempotent_approval_and_duplicate_handling(
    async_session_factory, policy_service, mock_redis
):
    import uuid

    from api.src.features.autopilot.schemas import ApprovePlanRequest
    from api.src.features.autopilot.service import AutopilotService
    from core.database.models import RoutingDecisionRecord
    from core.routing.snapshot import TicketSnapshotFactory

    mock_client = AsyncMock()
    dummy_task = TaskDTO(
        id=606,
        service_id=63,
        name="Доступ Wi-Fi",
        description="Прошу доступ",
        status_id=1,
        entities=ExtractedEntitiesDTO(),
    )
    mock_client.get_task.return_value = dummy_task
    mock_client.get_task_lifetime.return_value = []

    service = AutopilotService(
        client=mock_client,
        auth_bootstrap=AsyncMock(),
        policy_service=policy_service,
        diagnostics_service=AsyncMock(),
    )

    snapshot = TicketSnapshotFactory.create(dummy_task, comments=[])

    async with async_session_factory() as session:
        decision_id = uuid.uuid4()
        plan_id = uuid.uuid4()
        preflight_id = uuid.uuid4()
        snapshot_hash = snapshot.snapshot_hash
        params_hash = compute_canonical_params_hash({})

        preflight_dto = PreflightResultDTO(
            id=preflight_id,
            scenario_key="grant_wlan",
            params_hash=params_hash,
            status="passed",
            checks=[PreflightCheckDTO(name="test", status="passed")],
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )

        plan_dto = AgentPlanDTO(
            task_id=606,
            decision_id=decision_id,
            plan_id=plan_id,
            scenario_key="grant_wlan",
            scenario_name="Wi-Fi",
            description="desc",
            analysis_state="ready",
            routing_state="selected",
            snapshot_hash=snapshot_hash,
            plan_hash="",
            decision_reason_codes=["exact_match"],
            proposed_action="grant_wlan",
            proposed_params={},
            suggested_comment="Доступ предоставлен",
            target_status_id=3,
            is_executable=True,
            missing_facts=[],
            preflight=preflight_dto,
        )
        from core.routing.preflight import compute_canonical_plan_hash_from_json
        plan_hash = compute_canonical_plan_hash_from_json(plan_dto.model_dump(mode="json"))
        plan_dto.plan_hash = plan_hash

        dec = RoutingDecisionRecord(
            id=decision_id,
            task_id=606,
            snapshot_hash=snapshot_hash,
            router_version="2.0.0",
            state="selected",
            selected_scenario="grant_wlan",
        )
        preflight = RoutingPreflightRecord(
            id=preflight_id,
            decision_id=decision_id,
            task_id=606,
            snapshot_hash=snapshot_hash,
            scenario_key="grant_wlan",
            params_hash=params_hash,
            status="passed",
            checks_json=[{"name": "test", "status": "passed"}],
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        plan = PreparedPlanRecord(
            id=plan_id,
            decision_id=decision_id,
            preflight_id=preflight_id,
            task_id=606,
            snapshot_hash=snapshot_hash,
            plan_hash=plan_hash,
            scenario_key="grant_wlan",
            plan_json=plan_dto.model_dump(mode="json"),
            state="ready",
        )
        session.add_all([dec, preflight, plan])
        await session.commit()

        # First approve
        approve_req = ApprovePlanRequest(
            decision_id=decision_id,
            plan_id=plan_id,
            plan_hash=plan_hash,
            snapshot_hash=snapshot_hash,
            expected_status_id=1,
            last_event_id=None,
        )
        with patch("api.src.core.task_dispatch.dispatch_command_task.kiq", new_callable=AsyncMock):
            res1 = await service.approve_plan(
                ticket_id=606,
                req=approve_req,
                session=session,
                redis_client=mock_redis,
                operator_username="test_supervisor",
            )
            assert res1.status == "approved"
            assert res1.is_duplicate is False
            command_id = res1.command_id

            # Second identical approve -> returns same command_id with is_duplicate = True
            res2 = await service.approve_plan(
                ticket_id=606,
                req=approve_req,
                session=session,
                redis_client=mock_redis,
                operator_username="test_supervisor",
            )
            assert res2.status == "approved"
            assert res2.is_duplicate is True
            assert res2.command_id == command_id


@pytest.mark.asyncio
async def test_manual_takeover_reports_partial_external_update(
    async_session_factory,
    policy_service,
    mock_redis,
):
    from api.src.features.autopilot.service import AutopilotService
    from core.intraservice.auth import ServiceAuthCredentials

    client = AsyncMock()
    client.update_task.side_effect = RuntimeError("IntraService unavailable")
    auth = AsyncMock()
    auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        login="bot",
        password="pwd",
        bot_user_id=999,
        auth_b64="Ym90OnB3ZA==",
    )
    service = AutopilotService(
        client=client,
        auth_bootstrap=auth,
        policy_service=policy_service,
        diagnostics_service=AsyncMock(),
    )
    decision_id = uuid.uuid4()
    plan_id = uuid.uuid4()
    snapshot_hash = "b" * 64
    plan_hash = "a" * 64

    async with async_session_factory() as session:
        session.add(
            RoutingDecisionRecord(
                id=decision_id,
                task_id=707,
                snapshot_hash=snapshot_hash,
                router_version="test",
                state="selected",
                selected_scenario="grant_wlan",
            )
        )
        session.add(
            PreparedPlanRecord(
                id=plan_id,
                task_id=707,
                decision_id=decision_id,
                snapshot_hash=snapshot_hash,
                plan_hash=plan_hash,
                scenario_key="grant_wlan",
                plan_json={"proposed_params": {"target_user": "ivanov.i"}},
                state="selected",
            )
        )
        await session.commit()

        result = await service.manual_takeover_plan(
            ticket_id=707,
            req=ManualTakeoverRequest(
                decision_id=decision_id,
                plan_id=plan_id,
                plan_hash=plan_hash,
                snapshot_hash=snapshot_hash,
                reason_tag="manual_takeover",
            ),
            session=session,
            redis_client=mock_redis,
            operator_username="operator",
        )

        assert result.status == "manual_takeover"
        assert result.external_update_succeeded is False
        assert result.warning is not None
        assert mock_redis.store["autopilot:abort:707"] == "1"
        feedback = (
            await session.execute(
                select(RoutingFeedbackRecord).where(RoutingFeedbackRecord.prepared_plan_id == plan_id)
            )
        ).scalar_one()
        assert feedback.verdict == "manual_takeover"
