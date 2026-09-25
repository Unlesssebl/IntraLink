"""API Integration tests for Autopilot governance slice."""

from typing import AsyncGenerator

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

    async def set(self, key: str, value: str, ex: int = 0):
        self.store[key] = value
        return True

    async def delete(self, key: str):
        return int(self.store.pop(key, None) is not None)


@pytest.fixture
async def test_db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

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
async def test_get_autopilot_policies_endpoint(test_db_session, override_policy_service):
    async def override_db():
        yield test_db_session

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
async def test_put_autopilot_policy_endpoint(test_db_session, override_policy_service):
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Update install_printer to FULL_AUTO
        payload = {"mode": "FULL_AUTO", "min_confidence": 0.95}
        put_res = await client.put("/api/v2/autopilot/policies/install_printer", json=payload)
        assert put_res.status_code == 200
        data = put_res.json()
        assert data["scenario_key"] == "install_printer"
        assert data["mode"] == "FULL_AUTO"
        assert data["min_confidence"] == 0.95
        assert not data["is_circuit_broken"]

        # 2. Invalid mode error check
        bad_res = await client.put(
            "/api/v2/autopilot/policies/install_printer",
            json={"mode": "INVALID_MODE"},
        )
        assert bad_res.status_code == 422

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reset_circuit_breaker_endpoint(test_db_session, override_policy_service):
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    # Simulate 3 failures
    for _ in range(3):
        await override_policy_service.record_failure("install_printer", session=test_db_session)

    policy_broken = await override_policy_service.get_policy("install_printer", session=test_db_session)
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
async def test_list_commands_and_stats_endpoints(test_db_session, override_policy_service):
    from core.database.models import CommandRecord

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    # Insert a dummy CommandRecord
    cmd = CommandRecord(
        idempotency_key="cmd_test_1",
        action="install_printer",
        executor="worker",
        status="succeeded",
        initiator="test",
        task_id=101,
    )
    test_db_session.add(cmd)
    await test_db_session.commit()

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
    test_db_session, override_policy_service, mock_redis
):
    from unittest.mock import AsyncMock, patch

    from api.src.core.redis import get_redis
    from api.src.features.autopilot.router import get_autopilot_service_dep
    from api.src.features.autopilot.service import AutopilotService
    from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO

    mock_client = AsyncMock()
    dummy_task = TaskDTO(
        id=777,
        service_id=62,
        service_name="Установка принтеров",
        name="Настройка принтера в кабинете 305",
        description="Установить сетевой принтер HP на ПК WKS-042",
        status_id=1,
        status_name="Новая",
        entities=ExtractedEntitiesDTO(pc_name="WKS-042", printer_model="HP LaserJet"),
    )
    mock_client.get_task.return_value = dummy_task
    mock_client.get_task_lifetime.return_value = []

    mock_diag = AsyncMock()
    mock_diag.diagnose_host.return_value = AsyncMock(model_dump=lambda: {"is_online": True, "avg_rtt": "1.2ms"})

    service = AutopilotService(
        client=mock_client,
        policy_service=override_policy_service,
        diagnostics_service=mock_diag,
    )

    async def override_db():
        yield test_db_session

    async def override_redis():
        yield mock_redis

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_redis] = override_redis
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service
    app.dependency_overrides[get_autopilot_service_dep] = lambda: service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. GET /plan/777
        plan_res = await client.get("/api/v2/autopilot/plan/777")
        assert plan_res.status_code == 200
        plan = plan_res.json()
        assert plan["task_id"] == 777
        assert plan["scenario_key"] == "install_printer"
        assert "WKS-042" in plan["candidate_hosts"]

        # 2. POST /plan/777/approve with OCC conflict check
        conflict_res = await client.post(
            "/api/v2/autopilot/plan/777/approve",
            json={"expected_status_id": 999},  # expected status 999 != actual status 1
        )
        assert conflict_res.status_code == 409
        assert "Статус заявки изменился" in conflict_res.json()["detail"]

        # 3. Successful approve
        with patch("api.src.core.task_dispatch.dispatch_command_task.kiq", new_callable=AsyncMock):
            approve_res = await client.post(
                "/api/v2/autopilot/plan/777/approve",
                json={"expected_status_id": 1, "override_comment": "Одобрено супервизором"},
            )
            assert approve_res.status_code == 200
            assert approve_res.json()["status"] == "approved"
            assert approve_res.json()["action"] == "install_printer"

        # 4. POST /plan/777/correct with secret sanitization
        with patch("api.src.core.task_dispatch.dispatch_command_task.kiq", new_callable=AsyncMock):
            correct_res = await client.post(
                "/api/v2/autopilot/plan/777/correct",
                json={
                    "expected_status_id": 1,
                    "corrected_scenario": "ad_password_reset",
                    "corrected_params": {"pc_name": "WKS-042", "user_password": "super_secret_password_123"},
                    "corrected_comment": "Сброшен пароль в AD",
                    "correction_tag": "wrong_scenario",
                    "operator_notes": "Заявитель просил сброс пароля, а не принтер",
                },
            )
            assert correct_res.status_code == 200
            assert correct_res.json()["status"] == "corrected"
            assert correct_res.json()["action"] == "ad_password_reset"

        # 5. GET /corrections and verify secrets redacted
        corr_res = await client.get("/api/v2/autopilot/corrections")
        assert corr_res.status_code == 200
        corrections = corr_res.json()["corrections"]
        assert len(corrections) >= 1
        last_c = corrections[0]
        assert last_c["corrected_scenario"] == "ad_password_reset"
        assert last_c["correction_tag"] == "wrong_scenario"
        assert last_c["corrected_params"]["user_password"] == "***REDACTED***"

        # 6. GET /corrections/export (JSONL format)
        export_res = await client.get("/api/v2/autopilot/corrections/export")
        assert export_res.status_code == 200
        assert "application/x-ndjson" in export_res.headers["content-type"]
        assert "ad_password_reset" in export_res.text

        # 7. POST /reclaim/777
        reclaim_res = await client.post("/api/v2/autopilot/reclaim/777")
        assert reclaim_res.status_code == 200
        assert reclaim_res.json()["status"] == "reclaimed"
        assert reclaim_res.json()["ticket_id"] == 777
        assert mock_client.update_task.await_count >= 1

    app.dependency_overrides.clear()
