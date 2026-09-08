import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.config import settings
from app.database.db import AsyncSessionLocal, DecisionStep
from app.services.decision_journal import DecisionJournalService, analysis_state


def task(task_id: int = 94001) -> dict:
    return {
        "Id": task_id,
        "StatusId": 31,
        "ServiceId": 19,
        "Name": "Установить принтер",
        "Description": "ПК ZTE1234, принтер 10.244.10.20, пароль: secret-value",
        "Attachments": [{"Id": 7, "Name": "screen.png"}],
    }


@pytest.mark.asyncio
async def test_same_snapshot_reuses_decision_and_force_creates_next_version():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        first = await journal.record_triage(
            task_id=94001,
            task=task(),
            history=[],
            decision={
                "rule_type": "printer_install",
                "comment": "Готово",
                "status_id": 29,
            },
        )
        repeated = await journal.record_triage(
            task_id=94001,
            task=task(),
            history=[],
            decision={
                "rule_type": "printer_install",
                "comment": "Готово",
                "status_id": 29,
            },
        )
        forced = await journal.record_triage(
            task_id=94001,
            task=task(),
            history=[],
            decision={
                "rule_type": "printer_install",
                "comment": "Готово",
                "status_id": 29,
            },
            force=True,
        )

        assert repeated.id == first.id
        assert forced.id != first.id
        assert forced.version == first.version + 1


@pytest.mark.asyncio
async def test_same_source_snapshot_does_not_create_version_for_changed_generated_text():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        first = await journal.record_triage(
            task_id=94008,
            task=task(94008),
            history=[],
            decision={
                "rule_type": "consultation",
                "comment": "Первый текст",
                "status_id": 27,
            },
        )
        repeated = await journal.record_triage(
            task_id=94008,
            task=task(94008),
            history=[],
            decision={
                "rule_type": "consultation",
                "comment": "Другой текст",
                "status_id": 27,
            },
        )
        assert repeated.id == first.id
        assert first.context_json["analysis_revision"] == settings.ANALYSIS_REVISION


@pytest.mark.asyncio
async def test_analysis_state_distinguishes_current_stale_and_unknown():
    async with AsyncSessionLocal() as db:
        source = task(94009)
        record = await DecisionJournalService(db).record_triage(
            task_id=94009,
            task=source,
            history=[],
            decision={
                "rule_type": "consultation",
                "comment": "Нужен ручной разбор",
                "status_id": 27,
                "_decision_envelope": {"scenario_key": "consultation", "outcome": {}},
            },
        )
        current = analysis_state(record, task=source, history=[])
        stale = analysis_state(
            record,
            task={**source, "Description": "Описание изменилось"},
            history=[],
        )
        unknown = analysis_state(record, task=source, freshness_known=False)

        assert current["state"] == "ready"
        assert current["scenario_key"] == "consultation"
        assert current["can_quick_apply"] is True
        assert stale["freshness"] == "stale"
        assert stale["can_quick_apply"] is False
        assert unknown["freshness"] == "unknown"
        assert unknown["can_quick_apply"] is False


@pytest.mark.asyncio
async def test_failed_reanalysis_keeps_result_but_blocks_application():
    async with AsyncSessionLocal() as db:
        source = task(94011)
        journal = DecisionJournalService(db)
        result = await journal.record_triage(
            task_id=94011,
            task=source,
            history=[],
            decision={
                "rule_type": "consultation",
                "comment": "Результат",
                "status_id": 27,
            },
        )
        failed = await journal.record_triage_failure(
            task_id=94011,
            task=source,
            history=[],
            error_code="analysis_failed",
            actor="operator:test",
        )

        latest_result = await journal.latest_triage(94011)
        latest_attempt = await journal.latest_triage_attempt(94011)
        state = analysis_state(
            latest_result,
            task=source,
            history=[],
            last_attempt=latest_attempt,
        )

        assert latest_result.id == result.id
        assert latest_attempt.id == failed.id
        assert state["has_result"] is True
        assert state["state"] == "failed"
        assert state["can_quick_apply"] is False
        with pytest.raises(HTTPException):
            await journal.require_current(
                decision_id=result.id,
                task_id=94011,
                version=result.version,
            )


@pytest.mark.asyncio
async def test_journal_redacts_secrets_and_reports_unread_attachment_as_limitation():
    async with AsyncSessionLocal() as db:
        history = [
            {"Id": value, "Comments": f"Комментарий {value}"} for value in range(1, 8)
        ]
        record = await DecisionJournalService(db).record_triage(
            task_id=94002,
            task=task(94002),
            history=history,
            decision={
                "rule_type": "printer_install",
                "comment": "Готово",
                "status_id": 29,
            },
            ai_text=None,
        )
        assert record.completeness_json["complete"] is True
        assert record.completeness_json["attachments_read"] == 0
        assert record.completeness_json["limitations"]
        assert record.context_json["history_omitted_ids"] == [1, 2]
        assert "secret-value" not in str(record.context_json)

        steps = list(
            (
                await db.scalars(
                    select(DecisionStep).where(DecisionStep.decision_id == record.id)
                )
            ).all()
        )
        ai_step = next(step for step in steps if step.component == "ai")
        assert ai_step.input_tokens is None
        assert ai_step.output_tokens is None


@pytest.mark.asyncio
async def test_deterministic_fallback_is_not_reported_as_ai_usage():
    async with AsyncSessionLocal() as db:
        record = await DecisionJournalService(db).record_triage(
            task_id=94006,
            task=task(94006),
            history=[],
            decision={
                "rule_type": "standard_in_work",
                "comment": "Fallback",
                "status_id": 27,
            },
            ai_text="Детерминированный ответ",
            ai_metadata={
                "ai_used": False,
                "backend": "deterministic",
                "fallback": True,
            },
        )
        assert record.source_json["ai"] is False
        ai_step = await db.scalar(
            select(DecisionStep).where(
                DecisionStep.decision_id == record.id,
                DecisionStep.component == "ai",
            )
        )
        assert ai_step is not None
        assert ai_step.status == "fallback"


@pytest.mark.asyncio
async def test_operational_decision_does_not_fabricate_rule_source():
    async with AsyncSessionLocal() as db:
        record = await DecisionJournalService(db).record_operational(
            task_id=94007,
            ticket_run_id=None,
            action="apply_triage",
            target={"task_id": 94007},
            parameters={"status_id": 27},
            actor="operator:test",
            task=task(94007),
            history=[],
        )
        assert record.source_json == {"rule": False, "rag": False, "ai": False}
        assert record.context_json["ticket_fingerprint"]


@pytest.mark.asyncio
async def test_operational_version_does_not_invalidate_current_triage_decision():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        triage = await journal.record_triage(
            task_id=94010,
            task=task(94010),
            history=[],
            decision={
                "rule_type": "consultation",
                "comment": "Разобрать",
                "status_id": 27,
            },
        )
        await journal.record_operational(
            task_id=94010,
            ticket_run_id=None,
            action="diagnose",
            target={"task_id": 94010},
            parameters={},
            actor="operator:test",
            task=task(94010),
            history=[],
        )
        current = await journal.require_current(
            decision_id=triage.id,
            task_id=94010,
            version=triage.version,
        )
        assert current.id == triage.id


@pytest.mark.asyncio
async def test_old_decision_is_rejected_after_new_version():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        first = await journal.record_triage(
            task_id=94003,
            task=task(94003),
            history=[],
            decision={
                "rule_type": "printer_install",
                "comment": "Первое",
                "status_id": 29,
            },
        )
        await journal.record_triage(
            task_id=94003,
            task={**task(94003), "Description": "Контекст изменился"},
            history=[],
            decision={
                "rule_type": "standard_in_work",
                "comment": "Второе",
                "status_id": 27,
            },
        )
        with pytest.raises(HTTPException) as exc:
            await journal.require_current(
                decision_id=uuid.UUID(str(first.id)),
                task_id=94003,
                version=first.version,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "decision_stale"


@pytest.mark.asyncio
async def test_feedback_recording_and_serialization():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        record = await journal.record_triage(
            task_id=94004,
            task=task(94004),
            history=[],
            decision={
                "rule_type": "printer_install",
                "comment": "Предложение",
                "status_id": 29,
            },
        )
        feedback = await journal.add_feedback(
            decision_id=record.id,
            verdict="correct",
            reason_code="wrong_rule",
            comment="Отличный регламент",
            final_action={"status_id": 29, "comment": "Выполнено вручную"},
            actor="operator:alen",
        )
        assert feedback.verdict == "correct"
        assert feedback.reason_code == "wrong_rule"
        assert feedback.final_action_json == {
            "status_id": 29,
            "comment": "Выполнено вручную",
        }
        assert feedback.actor == "operator:alen"

        from app.database.db import DecisionFeedback

        feedback_list = list(
            (
                await db.scalars(
                    select(DecisionFeedback).where(
                        DecisionFeedback.decision_id == record.id
                    )
                )
            ).all()
        )
        assert len(feedback_list) == 1
        assert feedback_list[0].verdict == "correct"
        assert feedback_list[0].actor == "operator:alen"


async def make_access_token(subject: str, role: str) -> str:
    from app.database.db import Principal, PrincipalRole
    from app.services.identity import ensure_rbac_catalog, issue_session

    async with AsyncSessionLocal() as db:
        await ensure_rbac_catalog(db, commit=False)
        principal = Principal(
            type="human",
            subject=subject,
            display_name=subject,
            status="active",
        )
        db.add(principal)
        await db.flush()
        db.add(PrincipalRole(principal_id=principal.id, role_name=role))
        await db.commit()
        token, _refresh, _expires = await issue_session(
            db,
            principal=principal,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
        return token


@pytest.mark.asyncio
async def test_decision_api_rbac_and_flow():
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        record = await journal.record_triage(
            task_id=94005,
            task=task(94005),
            history=[],
            decision={
                "rule_type": "printer_install",
                "comment": "API test",
                "status_id": 29,
            },
        )

    operator_token = await make_access_token(
        "test.decision.operator", "helpdesk_operator"
    )
    admin_token = await make_access_token("test.decision.admin", "system_admin")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Without auth -> 401
        res_no_auth = await client.get("/api/v2/tasks/94005/decisions")
        assert res_no_auth.status_code == 401

        # 2. Operator lacks audit:read -> 403
        res_op = await client.get(
            "/api/v2/tasks/94005/decisions",
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert res_op.status_code == 403

        # 3. Admin has audit:read and triage:read -> 200
        res_admin = await client.get(
            "/api/v2/tasks/94005/decisions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_admin.status_code == 200
        data = res_admin.json()
        assert len(data["items"]) >= 1
        assert data["items"][0]["id"] == str(record.id)

        # 4. Get decision details -> 200
        res_detail = await client.get(
            f"/api/v2/decisions/{record.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_detail.status_code == 200
        assert res_detail.json()["id"] == str(record.id)

        # 5. Post feedback with admin -> 201
        res_feedback = await client.post(
            f"/api/v2/decisions/{record.id}/feedback",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "verdict": "correct",
                "reason_code": "wrong_rule",
                "comment": "Всё верно",
                "final_action": {"status_id": 29},
            },
        )
        assert res_feedback.status_code == 201
        assert res_feedback.json()["verdict"] == "correct"
