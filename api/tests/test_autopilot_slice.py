"""API Integration tests for Autopilot governance slice."""


import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.core.db import get_db_session
from api.src.features.autopilot.router import get_policy_service_dep
from api.src.main import app
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base


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
async def test_db_factory():
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
def override_policy_service(mock_redis) -> AutopilotPolicyService:
    return AutopilotPolicyService(redis_client=mock_redis)


def test_policy_dependency_uses_resolved_redis_client(mock_redis):
    from api.src.features.autopilot.router import get_policy_service_dep

    service = get_policy_service_dep(redis=mock_redis)
    assert service.redis_client is mock_redis


@pytest.mark.asyncio
async def test_get_autopilot_policies_endpoint(test_db_factory, override_policy_service):
    async def override_db():
        async with test_db_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/v2/autopilot/policies")
        assert res.status_code == 200
        data = res.json()
        assert "policies" in data
        assert data["total"] >= 3
        keys = [p["scenario_key"] for p in data["policies"]]
        assert "install_printer" in keys
        assert "ad_password_reset" in keys
        assert "rag_consultation" in keys

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_put_autopilot_policy_endpoint(test_db_factory, override_policy_service):
    async def override_db():
        async with test_db_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Update install_printer to FULL_AUTO
        payload = {"mode": "FULL_AUTO"}
        put_res = await client.put("/api/v2/autopilot/policies/install_printer", json=payload)
        assert put_res.status_code == 200
        data = put_res.json()
        assert data["scenario_key"] == "install_printer"
        assert data["mode"] == "FULL_AUTO"
        assert not data["is_circuit_broken"]

        # 2. Invalid mode error check
        bad_res = await client.put(
            "/api/v2/autopilot/policies/install_printer",
            json={"mode": "INVALID_MODE"},
        )
        assert bad_res.status_code == 422

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reset_circuit_breaker_endpoint(test_db_factory, override_policy_service):
    async def override_db():
        async with test_db_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    # Simulate 3 failures
    async with test_db_factory() as s:
        for _ in range(3):
            await override_policy_service.record_failure("install_printer", session=s)

    async with test_db_factory() as s:
        policy_broken = await override_policy_service.get_policy("install_printer", session=s)
        assert policy_broken.is_circuit_broken is True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post("/api/v2/autopilot/policies/install_printer/reset")
        assert res.status_code == 200
        data = res.json()
        assert data["is_circuit_broken"] is False
        assert data["consecutive_failures"] == 0

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_commands_and_stats_endpoints(test_db_factory, override_policy_service):
    from core.database.models import CommandRecord

    async def override_db():
        async with test_db_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    # Insert a dummy CommandRecord
    async with test_db_factory() as s:
        cmd = CommandRecord(
            idempotency_key="cmd_test_1",
            action="install_printer",
            executor="worker",
            status="succeeded",
            initiator="test",
            task_id=101,
        )
        s.add(cmd)
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Commands feed
        feed_res = await client.get("/api/v2/autopilot/commands")
        assert feed_res.status_code == 200
        commands = feed_res.json()
        assert len(commands) >= 1
        assert commands[0]["action"] == "install_printer"
        assert commands[0]["status"] == "succeeded"

        # Stats
        stats_res = await client.get("/api/v2/autopilot/stats")
        assert stats_res.status_code == 200
        stats = stats_res.json()
        assert stats["total_automated_actions"] >= 1
        assert stats["hours_saved"] >= 0.2
        assert stats["active_scenarios_count"] >= 3

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_supervisor_plan_approve_and_correct_endpoints(
    test_db_factory, override_policy_service, mock_redis
):
    import uuid
    from unittest.mock import AsyncMock, patch

    from api.src.core.redis import get_redis
    from api.src.features.autopilot.router import get_autopilot_service_dep
    from api.src.features.autopilot.service import AutopilotService
    from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO

    mock_client = AsyncMock()
    dummy_task = TaskDTO(
        id=777,
        service_id=63,
        service_name="Предоставление доступа к корпоративной сети Wi-Fi",
        name="Доступ к сети Wi-Fi",
        description="Прошу предоставить доступ к Wi-Fi для сотрудника ivanov.i",
        status_id=1,
        status_name="Новая",
        executor_ids="999",
        entities=ExtractedEntitiesDTO(target_user="ivanov.i"),
    )
    mock_client.get_task.return_value = dummy_task
    mock_client.get_task_lifetime.return_value = []
    mock_client.get_current_user.return_value = {"Id": 999, "Login": "service_bot"}

    mock_diag = AsyncMock()
    mock_diag.diagnose_host.return_value = AsyncMock(model_dump=lambda: {"is_online": True, "avg_rtt": "1.2ms"})

    from core.intraservice.auth import ServiceAuthCredentials
    from core.routing.analysis_service import TicketAnalysisService
    from core.routing.candidate_generator import CandidateGenerator
    from core.routing.cascade import RoutingCascade
    from core.routing.catalog_service import ScenarioCatalogService
    from core.routing.persistence import RoutingDecisionService
    from core.routing.profile_registry import get_default_profile_registry
    from core.routing.providers.catalog import CatalogCandidateProvider
    from core.routing.providers.lexical import LexicalCandidateProvider
    from core.routing.providers.semantic import SemanticCandidateProvider
    from core.routing.verifier.service import GreyZoneVerifier
    class FakeVerifierTransport:
        async def complete_json(self, *args, **kwargs) -> str:
            return '{"verification_verdict": "supports", "confidence_score": 0.95, "reasoning": "Matches test"}'

    fake_embedder = AsyncMock(return_value=[0.0] * 1536)
    semantic_provider = SemanticCandidateProvider(embedder_fn=fake_embedder)
    candidate_generator = CandidateGenerator(
        registry=get_default_profile_registry(),
        providers=[CatalogCandidateProvider(), LexicalCandidateProvider(), semantic_provider],
    )
    cascade = RoutingCascade(
        candidate_generator=candidate_generator,
        verifier=GreyZoneVerifier(transport=FakeVerifierTransport()),
        profile_registry=get_default_profile_registry(),
    )
    decision_service = RoutingDecisionService(cascade=cascade)
    analysis_service = TicketAnalysisService(
        client=mock_client,
        decision_service=decision_service,
        policy_service=override_policy_service,
        session_factory=test_db_factory,
    )
    catalog_service = ScenarioCatalogService(
        policy_service=override_policy_service,
    )
    mock_auth_bootstrap = AsyncMock()
    mock_auth_bootstrap.bootstrap_auth.return_value = ServiceAuthCredentials(
        login="bot", password="pwd", bot_user_id=999, auth_b64="Ym90OnB3ZA=="
    )

    service = AutopilotService(
        client=mock_client,
        auth_bootstrap=mock_auth_bootstrap,
        policy_service=override_policy_service,
        diagnostics_service=mock_diag,
        analysis_service=analysis_service,
        catalog_service=catalog_service,
    )

    async def override_db():
        async with test_db_factory() as session:
            yield session

    async def override_redis():
        yield mock_redis

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_redis] = override_redis
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service
    app.dependency_overrides[get_autopilot_service_dep] = lambda: service

    from datetime import UTC, datetime, timedelta

    from core.autopilot.dto import PreflightCheckDTO, PreflightResultDTO
    from core.routing.preflight import compute_canonical_params_hash

    async def fake_execute_preflight(decision_id, snapshot, scenario_key, params, **kwargs):
        return PreflightResultDTO(
            id=uuid.uuid4(),
            scenario_key=scenario_key,
            params_hash=compute_canonical_params_hash(params),
            status="passed",
            checks=[PreflightCheckDTO(name="check", status="passed", message="OK")],
            expires_at=datetime.now(UTC) + timedelta(seconds=120),
        )

    with patch.object(service.analysis_service.preflight_service, "execute_preflight", side_effect=fake_execute_preflight), \
         patch.object(service.preflight_service, "execute_preflight", side_effect=fake_execute_preflight):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. GET /plan/777 before analyze -> analysis_state = not_analyzed
            plan_res_initial = await client.get("/api/v2/autopilot/plan/777")
            assert plan_res_initial.status_code == 200
            assert plan_res_initial.json()["analysis_state"] == "not_analyzed"

            # 2. POST /plan/777/analyze -> generates and stores prepared plan
            analyze_res = await client.post("/api/v2/autopilot/plan/777/analyze")
            assert analyze_res.status_code == 200
            plan = analyze_res.json()
            assert plan["task_id"] == 777
            assert plan["scenario_key"] == "grant_wlan"
            assert plan["analysis_state"] == "ready"
            assert plan["is_executable"] is True
            assert plan["plan_hash"] is not None

            # 3. GET /plan/777 after analyze -> returns stored plan strictly read-only
            plan_res_cached = await client.get("/api/v2/autopilot/plan/777")
            assert plan_res_cached.status_code == 200
            assert plan_res_cached.json()["plan_hash"] == plan["plan_hash"]

            # 4. Validation error (422) if required binding fields missing
            incomplete_res = await client.post(
                "/api/v2/autopilot/plan/777/approve",
                json={
                    "expected_status_id": 1,
                    # missing decision_id, plan_id, plan_hash, snapshot_hash
                },
            )
            assert incomplete_res.status_code == 422

            # 5. POST /plan/777/approve with OCC conflict check
            conflict_res = await client.post(
                "/api/v2/autopilot/plan/777/approve",
                json={
                    "expected_status_id": 999,  # expected status 999 != actual status 1
                    "decision_id": plan["decision_id"],
                    "snapshot_hash": plan["snapshot_hash"],
                    "plan_id": plan["plan_id"],
                    "plan_hash": plan["plan_hash"],
                    "last_event_id": plan.get("last_event_id"),
                },
            )
            assert conflict_res.status_code == 409
            assert "Статус заявки изменился" in conflict_res.json()["detail"]

            # Unknown/legacy actions are rejected before a terminal decision exists.
            legacy_res = await client.post(
                "/api/v2/autopilot/plan/777/correct",
                json={
                    "expected_status_id": 1,
                    "decision_id": plan["decision_id"],
                    "snapshot_hash": plan["snapshot_hash"],
                    "plan_id": plan["plan_id"],
                    "plan_hash": plan["plan_hash"],
                    "last_event_id": plan.get("last_event_id"),
                    "corrected_scenario": "cancel_ticket",
                    "corrected_params": {},
                    "correction_tag": "wrong_scenario",
                },
            )
            assert legacy_res.status_code == 422
            assert "cancel_ticket" in legacy_res.json()["detail"]

            # 6. Successful approve
            with patch("api.src.core.task_dispatch.dispatch_command_task.kiq", new_callable=AsyncMock):
                approve_res = await client.post(
                    "/api/v2/autopilot/plan/777/approve",
                    json={
                        "expected_status_id": 1,
                        "decision_id": plan["decision_id"],
                        "snapshot_hash": plan["snapshot_hash"],
                        "plan_id": plan["plan_id"],
                        "plan_hash": plan["plan_hash"],
                        "last_event_id": plan.get("last_event_id"),
                    },
                )
                assert approve_res.status_code == 200, approve_res.json()
                assert approve_res.json()["status"] == "approved"
                assert approve_res.json()["action"] == "grant_wlan"

            # A terminal approval cannot be replaced by a correction.
            blocked_correction = await client.post(
                "/api/v2/autopilot/plan/777/correct",
                json={
                    "expected_status_id": 1,
                    "decision_id": plan["decision_id"],
                    "snapshot_hash": plan["snapshot_hash"],
                    "plan_id": plan["plan_id"],
                    "plan_hash": plan["plan_hash"],
                    "last_event_id": plan.get("last_event_id"),
                    "corrected_scenario": "account_lock",
                    "corrected_params": {"target_user": "petrov.p"},
                    "correction_tag": "wrong_scenario",
                },
            )
            assert blocked_correction.status_code == 409

            # Reanalysis creates a new review subject for the correction path.
            reanalyze_res = await client.post("/api/v2/autopilot/plan/777/reanalyze")
            assert reanalyze_res.status_code == 200
            plan = reanalyze_res.json()

            # 7. POST /plan/777/correct with secret sanitization
            with patch("api.src.core.task_dispatch.dispatch_command_task.kiq", new_callable=AsyncMock):
                correct_res = await client.post(
                    "/api/v2/autopilot/plan/777/correct",
                    json={
                        "expected_status_id": 1,
                        "decision_id": plan["decision_id"],
                        "snapshot_hash": plan["snapshot_hash"],
                        "plan_id": plan["plan_id"],
                        "plan_hash": plan["plan_hash"],
                        "last_event_id": plan.get("last_event_id"),
                        "corrected_scenario": "account_lock",
                        "corrected_params": {"target_user": "petrov.p", "user_password": "super_secret_password_123"},
                        "corrected_comment": "Учетная запись заблокирована",
                        "correction_tag": "wrong_scenario",
                        "operator_notes": "Заявитель просил блокировку, а не Wi-Fi",
                    },
                )
                assert correct_res.status_code == 200, correct_res.json()
                assert correct_res.json()["status"] == "corrected"
                assert correct_res.json()["action"] == "account_lock"

            from sqlalchemy import select

            from core.database.models import CommandRecord, PreparedPlanRecord

            async with test_db_factory() as session:
                corrected_command = (
                    await session.execute(
                        select(CommandRecord).where(
                            CommandRecord.id == uuid.UUID(correct_res.json()["command_id"])
                        )
                    )
                ).scalar_one()
                corrected_plan = (
                    await session.execute(
                        select(PreparedPlanRecord).where(PreparedPlanRecord.id == corrected_command.plan_id)
                    )
                ).scalar_one()
                persisted_artifacts = f"{corrected_command.params_json}\n{corrected_plan.plan_json}"
                assert "super_secret_password_123" not in persisted_artifacts
                assert corrected_command.params_json["user_password"] == "***REDACTED***"
                assert corrected_plan.plan_json["proposed_params"]["user_password"] == "***REDACTED***"

            # 9. GET /scenarios/catalog
            catalog_res = await client.get("/api/v2/autopilot/scenarios/catalog")
            assert catalog_res.status_code == 200
            scenarios = catalog_res.json()["scenarios"]
            assert len(scenarios) >= 3
            scenario_keys = [s["scenario_key"] for s in scenarios]
            assert "grant_wlan" in scenario_keys
            assert "install_printer" in scenario_keys

            # 10. GET /feedback and verify secrets redacted
            feedback_res = await client.get("/api/v2/autopilot/feedback")
            assert feedback_res.status_code == 200
            feedbacks = feedback_res.json()["feedback"]
            assert len(feedbacks) >= 2
            corrected_fb = next(f for f in feedbacks if f["verdict"] == "corrected")
            assert corrected_fb["corrected_scenario"] == "account_lock"
            assert corrected_fb["reason_tag"] == "wrong_scenario"
            assert corrected_fb["corrected_params"]["user_password"] == "***REDACTED***"

            # 11. GET /feedback/export (JSONL format)
            export_res = await client.get("/api/v2/autopilot/feedback/export")
            assert export_res.status_code == 200
            assert "application/x-ndjson" in export_res.headers["content-type"]
            assert "account_lock" in export_res.text
            assert "super_secret_password_123" not in export_res.text

            # 12. GET /metrics/quality
            metrics_res = await client.get("/api/v2/autopilot/metrics/quality")
            assert metrics_res.status_code == 200
            metrics_data = metrics_res.json()["metrics"]
            assert metrics_data["total_decisions"] >= 1
            assert metrics_data["correction_rate"] > 0.0

            # 13. POST /reclaim/777
            reclaim_res = await client.post("/api/v2/autopilot/reclaim/777")
            assert reclaim_res.status_code == 200
            assert reclaim_res.json()["status"] == "reclaimed"
            assert reclaim_res.json()["ticket_id"] == 777
            assert mock_client.update_task.await_count >= 1

    app.dependency_overrides.clear()
