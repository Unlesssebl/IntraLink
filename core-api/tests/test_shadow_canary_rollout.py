"""Tests for Stage 2: Shadow comparison, Canary management, and Emergency Rollback."""

import uuid
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database.db import (
    AsyncSessionLocal,
    AutopilotScenario,
    TicketRun,
    TicketRunEvent,
)
from app.services.scenarios.shadow_comparator import ShadowComparator
from app.services.ticket_runs import TicketRunService, TicketRunState
from app.services.scenario_orchestrator import TicketRunOrchestrator
from shared.domain import (
    ActionProposed,
    CandidateOutcome,
    ClarificationRequired,
    DecisionEnvelope,
    Evidence,
    ManualReviewRequired,
)


def _make_dummy_envelope(
    scenario_key: str,
    outcome,
    confidence: float = 0.95,
    facts_summary: dict | None = None,
) -> DecisionEnvelope:
    return DecisionEnvelope(
        decision_version=1,
        scenario_key=scenario_key,
        scenario_version=1,
        facts_revision=1,
        facts_summary=facts_summary or {},
        candidates=[],
        outcome=outcome,
        policy={},
        response_draft="draft",
        evidence_refs=[],
        confidence=confidence,
        requires_approval=False,
        status="proposed",
    )


def test_shadow_comparator_matched_and_diverged():
    """Проверка компаратора: совпадение эквивалентных сценариев и детекция расхождений."""
    # 1. Совпадение create_user и user_creation
    outcome_action = ActionProposed(
        rule_key="r1",
        rule_version="1",
        outcome_key="user_creation",
        risk_level=1,
        action="create_user",
        parameters={"login": "test.user"},
        evidence=[],
    )
    task_user = {
        "Id": 101,
        "ServiceId": 53,
        "_field_meta": {"raw": {"1057": "Тестов", "1058": "Тест", "1065": "Инженер"}},
    }
    env1 = _make_dummy_envelope("create_user", outcome_action, confidence=0.95)
    res1 = ShadowComparator.compare(
        legacy_scenario_key="user_creation",
        envelope=env1,
        task=task_user,
    )
    assert res1.matched is True
    assert res1.diverged is False
    assert res1.scenario_matched is True
    assert len(res1.divergence_reasons) == 0

    # 2. Несовпадение сценариев: legacy ожидает user_creation, а модель вернула install_printer
    env2 = _make_dummy_envelope("install_printer", outcome_action, confidence=0.90)
    res2 = ShadowComparator.compare(
        legacy_scenario_key="user_creation",
        envelope=env2,
        task=task_user,
    )
    assert res2.diverged is True
    assert any("scenario_mismatch" in r for r in res2.divergence_reasons)

    # 3. Низкая уверенность
    env3 = _make_dummy_envelope("create_user", outcome_action, confidence=0.55)
    res3 = ShadowComparator.compare(
        legacy_scenario_key="user_creation",
        envelope=env3,
        task=task_user,
    )
    assert res3.diverged is True
    assert any("low_confidence" in r for r in res3.divergence_reasons)

    # 4. Несовпадение исходов: legacy не имеет базовых полей, а оркестратор предложил действие
    task_empty_user = {"Id": 102, "ServiceId": 53, "_field_meta": {"raw": {}}}
    res4 = ShadowComparator.compare(
        legacy_scenario_key="user_creation",
        envelope=env1,
        task=task_empty_user,
    )
    assert res4.diverged is True
    assert any("outcome_mismatch" in r for r in res4.divergence_reasons)


@pytest.mark.asyncio
async def test_record_shadow_and_metrics():
    """Проверка записи теневых событий и вычисления метрик расхождений."""
    async with AsyncSessionLocal() as db:
        run_id = uuid.uuid4()
        run = TicketRun(
            id=run_id,
            task_id=93001,
            mode="manual",
            state=TicketRunState.RUNNING.value,
            trigger_kind="ticket_created",
            trigger_key="ticket:93001:created",
            trigger_snapshot_json={"scenario_key": "user_creation"},
            scenario_key="user_creation",
            scenario_version=1,
            fact_revision=1,
            decision_version=1,
            version=1,
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()

        task = {
            "Id": 93001,
            "ServiceId": 53,
            "Name": "Создать пользователя",
            "_field_meta": {
                "raw": {
                    "1057": "Петров",
                    "1058": "Петр",
                    "1065": "Аналитик",
                    "1064": "ИТ",
                    "1074": "Интра",
                }
            },
        }

        orchestrator = TicketRunOrchestrator(db)
        envelope = await orchestrator.record_shadow(
            run_id=run.id,
            task=task,
            legacy_scenario_key="user_creation",
        )
        assert envelope.scenario_key == "create_user"

        # Проверяем получение метрик через TicketRunService
        service = TicketRunService(db)
        metrics = await service.get_shadow_metrics(days=1)
        assert metrics["summary"]["total_evaluations"] >= 1
        assert "create_user" in metrics["by_scenario"]
        assert metrics["summary"]["matched"] >= 1


@pytest.mark.asyncio
async def test_emergency_rollback_service_and_api():
    """Проверка сервиса и API аварийного отката (Emergency Rollback) в legacy."""
    async with AsyncSessionLocal() as db:
        service = TicketRunService(db)

        # Создаем канареечный и активный сценарии
        sc1 = await service.upsert_scenario(
            service_id=53,
            scenario_key="create_user",
            enabled=True,
            rollout_mode="canary",
            config={"canary_percent": 25},
            actor="test",
            expected_version=None,
        )
        sc2 = await service.upsert_scenario(
            service_id=19,
            scenario_key="install_printer",
            enabled=True,
            rollout_mode="active",
            config={},
            actor="test",
            expected_version=None,
        )
        assert sc1.rollout_mode == "canary"
        assert sc2.rollout_mode == "active"

        # Выполняем rollback всех сценариев
        rolled_back = await service.rollback_scenarios(
            target_mode="legacy",
            actor="admin",
            reason="Инцидент на канарейке",
        )
        assert len(rolled_back) >= 2
        for sc in rolled_back:
            assert sc.rollout_mode == "legacy"


@pytest.mark.asyncio
async def test_shadow_and_rollback_api_endpoints():
    """Проверка HTTP API эндпоинтов /scenarios, /rollback и /shadow/metrics."""
    from app.services.identity import create_service_credential
    async with AsyncSessionLocal() as db:
        _principal, credential, secret = await create_service_credential(
            db,
            subject="test-admin",
            display_name="Test Admin",
            scopes={"autopilot:manage", "autopilot:read"},
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {
            "X-Service-Key-Id": credential.key_id,
            "X-Service-Secret": secret,
            "Origin": "http://127.0.0.1:8000",
        }

        # 1. Проверка установки canary_percent через PUT /api/v2/autopilot/scenarios
        put_res = await client.put(
            "/api/v2/autopilot/scenarios",
            headers=headers,
            json={
                "service_id": 38,
                "scenario_key": "grant_wlan",
                "enabled": True,
                "rollout_mode": "canary",
                "canary_percent": 15,
            },
        )
        assert put_res.status_code == 200, put_res.text
        data = put_res.json()
        assert data["rollout_mode"] == "canary"
        assert data["canary_percent"] == 15

        # 2. Проверка GET /api/v2/autopilot/shadow/metrics
        metrics_res = await client.get(
            "/api/v2/autopilot/shadow/metrics?days=7",
            headers=headers,
        )
        assert metrics_res.status_code == 200, metrics_res.text
        metrics_data = metrics_res.json()
        assert "summary" in metrics_data
        assert "by_scenario" in metrics_data
        assert "recent_divergences" in metrics_data

        # 3. Проверка POST /api/v2/autopilot/scenarios/rollback
        rollback_res = await client.post(
            "/api/v2/autopilot/scenarios/rollback",
            headers=headers,
            json={
                "target_mode": "legacy",
                "reason": "Тестовый экстренный сброс в legacy",
            },
        )
        assert rollback_res.status_code == 200, rollback_res.text
        rb_data = rollback_res.json()
        assert rb_data["status"] == "rolled_back"
        assert rb_data["target_mode"] == "legacy"
        assert rb_data["count"] >= 1
