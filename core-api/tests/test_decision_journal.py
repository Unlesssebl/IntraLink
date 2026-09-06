import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database.db import AsyncSessionLocal, DecisionStep
from app.services.decision_journal import DecisionJournalService


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
            decision={"rule_type": "printer_install", "comment": "Готово", "status_id": 29},
        )
        repeated = await journal.record_triage(
            task_id=94001,
            task=task(),
            history=[],
            decision={"rule_type": "printer_install", "comment": "Готово", "status_id": 29},
        )
        forced = await journal.record_triage(
            task_id=94001,
            task=task(),
            history=[],
            decision={"rule_type": "printer_install", "comment": "Готово", "status_id": 29},
            force=True,
        )

        assert repeated.id == first.id
        assert forced.id != first.id
        assert forced.version == first.version + 1


@pytest.mark.asyncio
async def test_journal_redacts_secrets_and_reports_unread_attachment_as_limitation():
    async with AsyncSessionLocal() as db:
        history = [
            {"Id": value, "Comments": f"Комментарий {value}"}
            for value in range(1, 8)
        ]
        record = await DecisionJournalService(db).record_triage(
            task_id=94002,
            task=task(94002),
            history=history,
            decision={"rule_type": "printer_install", "comment": "Готово", "status_id": 29},
            ai_text=None,
        )
        assert record.completeness_json["complete"] is True
        assert record.completeness_json["attachments_read"] == 0
        assert record.completeness_json["limitations"]
        assert record.context_json["history_omitted_ids"] == [1, 2]
        assert "secret-value" not in str(record.context_json)

        steps = list(
            (await db.scalars(select(DecisionStep).where(DecisionStep.decision_id == record.id))).all()
        )
        ai_step = next(step for step in steps if step.component == "ai")
        assert ai_step.input_tokens is None
        assert ai_step.output_tokens is None


@pytest.mark.asyncio
async def test_old_decision_is_rejected_after_new_version():
    async with AsyncSessionLocal() as db:
        journal = DecisionJournalService(db)
        first = await journal.record_triage(
            task_id=94003,
            task=task(94003),
            history=[],
            decision={"rule_type": "printer_install", "comment": "Первое", "status_id": 29},
        )
        await journal.record_triage(
            task_id=94003,
            task={**task(94003), "Description": "Контекст изменился"},
            history=[],
            decision={"rule_type": "standard_in_work", "comment": "Второе", "status_id": 27},
        )
        with pytest.raises(HTTPException) as exc:
            await journal.require_current(
                decision_id=uuid.UUID(str(first.id)), task_id=94003, version=first.version
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "decision_stale"
