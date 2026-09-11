import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.database.db import (
    AsyncSessionLocal,
    DecisionApplicationAttempt,
    DecisionApplicationRequest,
    DecisionStep,
)
from app.main import app
from app.services.decision_analytics import DecisionFeedbackAnalyticsService
from app.services.decision_application_service import DecisionApplicationService
from app.services.decision_journal import (
    DecisionJournalService,
    calculate_comment_diff,
)
from app.services.intraservice import MutationOutcome, MutationResult

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


# ---------------------------------------------------------------------------
# 1. ТЕСТЫ РАСЧЕТА DIFF КОММЕНТАРИЕВ
# ---------------------------------------------------------------------------


def test_calculate_comment_diff_identical():
    proposed = "Уважаемый пользователь, заявка выполнена."
    applied = "Уважаемый пользователь, заявка выполнена."
    diff = calculate_comment_diff(proposed, applied)
    assert diff["identical"] is True
    assert diff["levenshtein_distance"] == 0
    assert diff["ratio"] == 0.0


def test_calculate_comment_diff_crlf_normalization():
    proposed = "Первая строка\r\nВторая строка\r\n"
    applied = "Первая строка\nВторая строка\n"
    diff = calculate_comment_diff(proposed, applied)
    assert diff["identical"] is True
    assert diff["levenshtein_distance"] == 0
    assert diff["ratio"] == 0.0


def test_calculate_comment_diff_unicode_russian_edit():
    proposed = "Доступ к Wi-Fi сети предоставлен."
    applied = "Доступ к Wi-Fi сети предоставлен. Проверьте подключение."
    diff = calculate_comment_diff(proposed, applied)
    assert diff["identical"] is False
    assert diff["levenshtein_distance"] > 0
    assert 0.0 < diff["ratio"] < 1.0
    assert diff["proposed_length"] == len(proposed)
    assert diff["applied_length"] == len(applied)


def test_calculate_comment_diff_empty_strings():
    diff = calculate_comment_diff("", "")
    assert diff["identical"] is True
    assert diff["levenshtein_distance"] == 0
    assert diff["ratio"] == 0.0

    diff2 = calculate_comment_diff(None, "Что-то написано")
    assert diff2["comparable"] is False
    assert diff2["identical"] is False


# ---------------------------------------------------------------------------
# 2. ТЕСТЫ ЖУРНАЛА РЕШЕНИЙ, ШАГОВ И ОБРАТНОЙ СВЯЗИ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_steps_persistence_in_journal():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        envelope_data = {
            "decision_id": str(uuid.uuid4()),
            "decision_version": 1,
            "scenario": {"scenario_id": "test_scenario", "rule_type": "consultation"},
            "facts": {"pc_name": "TEST-PC"},
            "policy": {"allowed": True, "status_id": 27, "expenses": 10},
            "outcome": {"action_type": "standard", "target_status_id": 27},
            "response": {"text": "Тестовый комментарий"},
            "steps": [
                {
                    "step_name": "facts_extract",
                    "step_index": 1,
                    "phase": "facts",
                    "status": "success",
                    "details": {"extracted_count": 1},
                },
                {
                    "step_name": "routing_match",
                    "step_index": 2,
                    "phase": "routing",
                    "status": "success",
                    "details": {"scenario_id": "test_scenario"},
                },
            ],
        }

        entry = await journal.record_envelope(
            task_id=99001,
            envelope=envelope_data,
            task={"Id": 99001, "StatusId": 31, "Name": "Тест шагов"},
            history=[],
        )

        assert entry is not None
        steps_stmt = select(DecisionStep).where(DecisionStep.decision_id == entry.id)
        saved_steps = (await db.execute(steps_stmt)).scalars().all()
        assert len(saved_steps) == 2
        assert saved_steps[0].component == "facts_extract"
        assert saved_steps[0].status == "success"
        assert saved_steps[1].component == "routing_match"


@pytest.mark.asyncio
async def test_feedback_persistence_and_deduplication():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        envelope_data = {
            "decision_id": str(uuid.uuid4()),
            "decision_version": 1,
            "scenario": {"scenario_id": "printer_install"},
            "facts": {},
            "policy": {"allowed": True, "status_id": 29},
            "outcome": {"action_type": "standard", "target_status_id": 29},
            "response": {"text": "Принтер установлен"},
            "steps": [],
        }
        entry = await journal.record_envelope(
            task_id=99002,
            envelope=envelope_data,
            task={"Id": 99002, "StatusId": 31},
            history=[],
        )

        event_id = str(uuid.uuid4())
        fb1 = await journal.add_feedback(
            decision_id=entry.id,
            verdict="rejected",
            source="explicit_feedback",
            event_id=event_id,
            operator_reason_code="wrong_scenario",
            comment="Это не принтер, а МФУ со сканером",
        )
        assert fb1.verdict == "rejected"
        assert fb1.operator_reason_code == "wrong_scenario"

        fb2 = await journal.add_feedback(
            decision_id=entry.id,
            verdict="rejected",
            source="explicit_feedback",
            event_id=event_id,
            operator_reason_code="wrong_scenario",
            comment="Это не принтер, а МФУ со сканером",
        )
        assert fb2.id == fb1.id


# ---------------------------------------------------------------------------
# 3. ТЕСТЫ ИЗОЛЯЦИИ DRY_RUN И IDEMPOTENCY В DECISION_APPLICATION_SERVICE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_application_dry_run_isolation():
    async with AsyncSessionLocal() as db:
        service = DecisionApplicationService(db)
        with patch("app.services.intraservice.update_task_full_structured", new_callable=AsyncMock) as mock_update:
            result = await service.execute_apply(
                request_id=uuid.uuid4(),
                task_ids=[99003],
                status_id=27,
                comment="Dry run test comment",
                expenses=15,
                service_auth_b64="bW9ja19hdXRo",
                dry_run=True,
            )

            assert result["dry_run"] is True
            assert result["status"] == "simulated"
            assert result["overall_outcome"] == "confirmed"
            assert mock_update.call_count == 0

            req = await db.scalar(
                select(DecisionApplicationRequest).where(
                    DecisionApplicationRequest.request_hash == "non_existent"
                )
            )
            assert req is None


@pytest.mark.asyncio
async def test_decision_application_idempotency_and_recording():
    async with AsyncSessionLocal() as db:
        service = DecisionApplicationService(db)

        mock_update_res = MutationResult(
            outcome=MutationOutcome.CONFIRMED,
            status_code=200,
            data={"Id": 99004, "StatusId": 27},
        )
        mock_exp_res = MutationResult(
            outcome=MutationOutcome.CONFIRMED,
            status_code=200,
            data={"Id": 1},
        )

        with patch("app.services.intraservice.update_task_full_structured", new_callable=AsyncMock, return_value=mock_update_res) as mock_update, \
             patch("app.services.intraservice.add_task_expenses_structured", new_callable=AsyncMock, return_value=mock_exp_res) as mock_exp, \
             patch("app.services.decision_application_service.enforce_triage_apply_rate_limit", new_callable=AsyncMock):

            request_id = uuid.uuid4()

            # Первый вызов
            res1 = await service.execute_apply(
                request_id=request_id,
                task_ids=[99004],
                status_id=27,
                comment="Первичное исполнение",
                expenses=10,
                service_auth_b64="bW9ja19hdXRo",
                dry_run=False,
            )

            assert res1["success"] is True
            assert res1["results"][0]["status"] == "succeeded"
            assert res1["results"][0]["update_ok"] is True
            assert res1["results"][0]["expenses_ok"] is True
            assert mock_update.call_count == 1

            # Повторный вызов с тем же request_id
            res2 = await service.execute_apply(
                request_id=request_id,
                task_ids=[99004],
                status_id=27,
                comment="Первичное исполнение",
                expenses=10,
                service_auth_b64="bW9ja19hdXRo",
                dry_run=False,
            )

            assert res2["success"] is True
            assert res2["results"][0]["status"] == "succeeded"
            assert res2["results"][0]["update_ok"] is True
            assert res2["results"][0]["expenses_ok"] is True
            # Сетевые вызовы не должны повторяться
            assert mock_update.call_count == 1


# ---------------------------------------------------------------------------
# 4. ТЕСТЫ RECONCILIATION ДЛЯ UNKNOWN ATTEMPT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_unknown_attempt():
    attempt_id = uuid.uuid4()
    req_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        # Создаем базовое решение для foreign key
        journal = DecisionJournalService(db)
        envelope_data = {
            "decision_id": str(uuid.uuid4()),
            "decision_version": 1,
            "scenario": {"scenario_id": "consultation"},
            "facts": {},
            "policy": {"allowed": True, "status_id": 29},
            "outcome": {"action_type": "standard", "target_status_id": 29},
            "response": {"text": "Решено"},
            "steps": [],
        }
        dec_entry = await journal.record_envelope(
            task_id=99005,
            envelope=envelope_data,
            task={"Id": 99005, "StatusId": 31},
            history=[],
        )

        req = DecisionApplicationRequest(
            request_id=req_id,
            actor="tester",
            request_hash="test_hash_reconcile",
            dry_run=False,
        )
        db.add(req)

        attempt = DecisionApplicationAttempt(
            id=attempt_id,
            request_id=req_id,
            task_id=99005,
            decision_id=dec_entry.id,
            decision_version=1,
            actor="tester",
            source="recommendation_apply",
            state="unknown",
            requested_action_json={"status_id": 29, "comment": "Решено"},
            proposed_action_json={"status_id": 29, "comment": "Решено"},
            suboperations_json={
                "target_update": {"outcome": "unknown"},
                "expenses": {"outcome": "not_sent"},
            },
        )
        db.add(attempt)
        await db.commit()

        service = DecisionApplicationService(db)

        with patch("app.services.intraservice.get_single_task", new_callable=AsyncMock) as mock_get_task, \
             patch("app.services.intraservice.get_task_lifetime", new_callable=AsyncMock) as mock_lifetime:
            mock_get_task.return_value = {
                "Id": 99005,
                "StatusId": 29,
            }
            mock_lifetime.return_value = {
                "TaskLifetimes": [
                    {"Comments": "Решено", "Created": "2026-09-12T00:00:00Z"}
                ]
            }

            reconcile_res = await service.reconcile_attempt(
                attempt_id=attempt_id,
                service_auth_b64="bW9ja19hdXRo",
            )
            assert reconcile_res["reconciled"] is True
            assert reconcile_res["outcome"] == "confirmed"

        updated_attempt = await db.get(DecisionApplicationAttempt, attempt_id)
        assert updated_attempt.state == "succeeded"


# ---------------------------------------------------------------------------
# 5. ТЕСТЫ АНАЛИТИКИ КАЧЕСТВА (DecisionFeedbackAnalyticsService)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_quality_analytics_calculation():
    analytics = DecisionFeedbackAnalyticsService(session_factory=AsyncSessionLocal)
    summary = await analytics.get_quality_summary(days=30)

    assert "sample_size" in summary
    assert "acceptance_rate" in summary
    assert "rejection_rate" in summary
    assert "status_override_rate" in summary
    assert "comment_edit_rate" in summary
    assert "top_rejected_scenarios" in summary
    assert "reconciliation_summary" in summary


# ---------------------------------------------------------------------------
# 6. ТЕСТЫ API ЭНДПОИНТОВ РОУТЕРА ТРИАЖА (POST /decision-feedback & GET /analytics)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_explicit_feedback_and_analytics():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        envelope_data = {
            "decision_id": str(uuid.uuid4()),
            "decision_version": 1,
            "scenario": {"scenario_id": "wlan_guest"},
            "facts": {},
            "policy": {"allowed": True, "status_id": 27},
            "outcome": {"action_type": "standard", "target_status_id": 27},
            "response": {"text": "Доступ открыт"},
            "steps": [],
        }
        entry = await journal.record_envelope(
            task_id=99006,
            envelope=envelope_data,
            task={"Id": 99006, "StatusId": 31},
            history=[],
        )
        decision_id = str(entry.id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        event_id = str(uuid.uuid4())
        resp = await client.post(
            "/api/v2/triage/tasks/99006/decision-feedback",
            headers=HEADERS,
            json={
                "decision_id": decision_id,
                "decision_version": 1,
                "event_id": event_id,
                "verdict": "rejected",
                "reason_code": "wrong_scenario",
                "comment": "Нужен корпоративный, а не гостевой",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "rejected"
        assert data["reason_code"] == "wrong_scenario"

        resp2 = await client.post(
            "/api/v2/triage/tasks/99006/decision-feedback",
            headers=HEADERS,
            json={
                "decision_id": decision_id,
                "decision_version": 1,
                "event_id": event_id,
                "verdict": "rejected",
                "reason_code": "wrong_scenario",
                "comment": "Нужен корпоративный, а не гостевой",
            },
        )
        assert resp2.status_code == 200

        analytics_resp = await client.get(
            "/api/v2/triage/analytics/quality?days=7",
            headers=HEADERS,
        )
        assert analytics_resp.status_code == 200
        analytics_data = analytics_resp.json()
        assert "metrics" in analytics_data
        assert "reconciliation_summary" in analytics_data
