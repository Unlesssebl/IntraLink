"""Tests for Stage 4: Interactive Fact-Override backend API."""

import pytest
import uuid
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database.db import (
    AsyncSessionLocal,
    AutopilotScenario,
    DecisionRecord,
    TicketRun,
    TicketRunEvent,
)
from app.services.identity import create_service_credential
from app.services.ticket_runs import TicketRunService, TicketRunState


from app.routers.deps import get_service_auth_b64


@pytest.fixture(autouse=True)
def override_service_auth():
    async def _mock_auth():
        return "mock-service-auth"

    app.dependency_overrides[get_service_auth_b64] = _mock_auth
    yield
    app.dependency_overrides.pop(get_service_auth_b64, None)


@pytest.fixture
async def auth_headers():
    async with AsyncSessionLocal() as db:
        _principal, credential, secret = await create_service_credential(
            db,
            subject="test-operator",
            display_name="Test Operator",
            scopes={"triage:mutate", "triage:read", "autopilot:read"},
        )
    return {
        "X-Service-Key-Id": credential.key_id,
        "X-Service-Secret": secret,
        "Origin": "http://127.0.0.1:8000",
    }


@pytest.mark.asyncio
async def test_override_facts_rejects_invalid_pc_name(auth_headers):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.services.triage_service.TriageService.get_task_card_details", new=AsyncMock(return_value={"task": {"Id": 12345}})):
            resp = await client.post(
                "/api/v2/tasks/12345/override-facts",
                headers=auth_headers,
                json={
                    "facts": {"pc_name": "PC; rm -rf /"},
                },
            )
            assert resp.status_code == 422, f"Got {resp.status_code}: {resp.text}"
            assert "Недопустимое сетевое имя компьютера" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_override_facts_version_conflict(auth_headers):
    async with AsyncSessionLocal() as db:
        db.add(DecisionRecord(
            task_id=98765,
            version=5,
            analysis_kind="triage",
            status="proposed",
            outcome="clarification",
            context_fingerprint=f"fp-{uuid.uuid4()}",
            proposal_json={"ready": True},
            completeness_json={},
            source_json={},
            policy_json={},
            created_by="test",
        ))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.services.triage_service.TriageService.get_task_card_details", new=AsyncMock(return_value={"task": {"Id": 98765}})):
            resp = await client.post(
                "/api/v2/tasks/98765/override-facts",
                headers=auth_headers,
                json={
                    "expected_decision_version": 4,  # Outdated! Version in DB is 5
                    "facts": {"pc_name": "WS-FINANCE-01"},
                },
            )
            assert resp.status_code == 409
            assert "Версия решения изменилась" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_override_facts_success_recompiles_envelope(auth_headers):
    task_id = 777123
    mock_task = {
        "Id": task_id,
        "Name": "Подключить принтер HP LaserJet",
        "Description": "Требуется установить новый принтер",
        "PrinterName": "HP LaserJet",
        "ServiceId": 19,
        "StatusId": 31,
    }

    async with AsyncSessionLocal() as db:
        scenario = await db.scalar(
            AutopilotScenario.__table__.select().where(
                AutopilotScenario.service_id == 19,
                AutopilotScenario.scenario_key == "install_printer",
            )
        )
        if scenario is None:
            db.add(AutopilotScenario(
                service_id=19,
                scenario_key="install_printer",
                enabled=True,
                rollout_mode="active",
                updated_by="test",
            ))
            await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.services.triage_service.TriageService.get_task_card_details", new=AsyncMock(return_value={
            "task": mock_task,
            "history": [],
            "telemetry": {"is_online": True},
            "suggested_action": {"version": 1},
        })):
            resp = await client.post(
                f"/api/v2/tasks/{task_id}/override-facts",
                headers=auth_headers,
                json={
                    "expected_decision_version": 1,
                    "facts": {
                        "pc_name": "WS-0123",
                        "printer_address": "10.20.30.40",
                    },
                    "current_draft_text": "Добрый день! Подключаю принтер на WS-0123.",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["task_id"] == task_id
            envelope = data["decision_envelope"]
            assert envelope["scenario_key"] == "install_printer"
            assert envelope["facts_summary"]["pc_name"]["value"] == "WKS0123"
            assert envelope["facts_summary"]["pc_name"]["source"] == "operator"
            assert envelope["facts_summary"]["printer_address"]["value"] == "10.20.30.40"
            assert envelope["facts_summary"]["printer_address"]["source"] == "operator"

            # Check ticket run and event created in DB
            async with AsyncSessionLocal() as db:
                run = await TicketRunService(db).get_latest_for_task(task_id)
                assert run is not None
                assert run.decision_version >= 2
                events = await TicketRunService(db).get_events(run.id)
                override_events = [e for e in events if e.event_type == "facts_overridden"]
                assert len(override_events) >= 1
