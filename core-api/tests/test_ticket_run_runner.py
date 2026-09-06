import pytest
import datetime as dt
from sqlalchemy import select

from app.database.db import AsyncSessionLocal, CommandRecord, TriageTemplate
from app.services.command_service import CommandService
from app.services.ticket_run_runner import TicketRunRunner
from app.services.ticket_runs import REQUIRED_AUTOPILOT_TEMPLATES, TicketRunService


async def seed_templates(db) -> None:
    statuses = {
        "printer_ip_clarify": 35,
        "pc_offline": 35,
        "ticket_timeout_cancel": 30,
        "ticket_not_relevant": 30,
        "autopilot_unsupported_cancel": 30,
        "autopilot_execution_failed_cancel": 30,
        "resolved_standard": 29,
    }
    texts = {
        "pc_offline": "ПК {pc_name} недоступен.",
        "autopilot_unsupported_cancel": "Не поддерживается: {reason}",
        "autopilot_execution_failed_cancel": "Ошибка установки: {reason}",
    }
    for key in REQUIRED_AUTOPILOT_TEMPLATES:
        existing = await db.scalar(select(TriageTemplate).where(TriageTemplate.key == key))
        if existing:
            existing.status_id = statuses[key]
            existing.template_text = texts.get(key, "Шаблон")
            existing.is_active = True
        else:
            db.add(
                TriageTemplate(
                    key=key,
                    name=key,
                    category="autopilot",
                    status_id=statuses[key],
                    status_name="Шаблон",
                    expenses=5,
                    template_text=texts.get(key, "Шаблон"),
                    is_active=True,
                )
            )
    await db.commit()


def printer_task(task_id: int) -> dict:
    return {
        "Id": task_id,
        "StatusId": 31,
        "ExecutorId": 10001,
        "Name": "HP LaserJet 9100",
        "CustomFields": [
            {"CustomFieldId": 1112, "Value": "PC-93001"},
            {"CustomFieldId": 1103, "Value": "10.20.30.40"},
        ],
    }


@pytest.mark.asyncio
async def test_printer_happy_path_uses_only_linked_v2_commands():
    async with AsyncSessionLocal() as db:
        await seed_templates(db)
        runs = TicketRunService(db)
        setting = await runs.get_global_setting()
        assert setting is not None
        await db.commit()
        await runs.set_global_enabled(enabled=True, actor="admin:test", expected_version=1)
        registration = await runs.register_assignment(
            task=printer_task(93001), assistant_user_id=10001, open_status_id=31
        )
        assert registration.run is not None
        run = registration.run
        runner = TicketRunRunner(db)

        await runner.advance(run_id=run.id, task=printer_task(93001))
        diagnose = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "diagnose_host",
            )
        )
        assert diagnose is not None
        claim = await CommandService(db).claim(diagnose.id, worker_id="test-windows")
        await CommandService(db).finish(
            diagnose.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"status": "success", "payload": {"diagnostics": {"is_online": True}}},
            error_message=None,
            worker_id="test-windows",
        )

        await runner.advance(run_id=run.id, task=printer_task(93001))
        install = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "install_printer",
            )
        )
        assert install is not None
        assert install.status == "awaiting_approval"
        await CommandService(db).approve(
            install.id, decision="approve", reason=None, operator="operator:test"
        )
        claim = await CommandService(db).claim(install.id, worker_id="test-windows")
        await CommandService(db).finish(
            install.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"status": "success", "payload": {"installed": True, "verified": True}},
            error_message=None,
            worker_id="test-windows",
        )

        await runner.advance(run_id=run.id, task=printer_task(93001))
        finalize = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "apply_triage",
            )
        )
        assert finalize is not None
        assert finalize.params_json["status_id"] == 29
        await CommandService(db).approve(
            finalize.id, decision="approve", reason=None, operator="operator:test"
        )
        claim = await CommandService(db).claim(finalize.id, worker_id="test-backend")
        await CommandService(db).finish(
            finalize.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"results": [{"update_ok": True}]},
            error_message=None,
            worker_id="test-backend",
        )

        completed = await runner.advance(run_id=run.id, task=printer_task(93001))
        assert completed.state == "completed"
        assert completed.outcome == "completed"
        assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_missing_required_template_stops_cycle_without_ticket_command():
    async with AsyncSessionLocal() as db:
        await seed_templates(db)
        template = await db.scalar(
            select(TriageTemplate).where(TriageTemplate.key == "printer_ip_clarify")
        )
        assert template is not None
        template.is_active = False
        await db.commit()
        runs = TicketRunService(db)
        setting = await runs.get_global_setting()
        assert setting is not None
        setting.enabled = True
        await db.commit()
        task = {
            "Id": 93002,
            "StatusId": 31,
            "ExecutorId": 10001,
            "Name": "Подключить принтер",
        }
        registration = await runs.register_assignment(
            task=task, assistant_user_id=10001, open_status_id=31
        )
        assert registration.run is not None

        stopped = await TicketRunRunner(db).advance(
            run_id=registration.run.id, task=task
        )
        assert stopped.state == "system_error"
        assert stopped.error_code == "template_invalid"
        command = await db.scalar(
            select(CommandRecord).where(CommandRecord.ticket_run_id == stopped.id)
        )
        assert command is None


@pytest.mark.asyncio
async def test_clarification_timeout_prepares_template_cancellation():
    async with AsyncSessionLocal() as db:
        await seed_templates(db)
        runs = TicketRunService(db)
        setting = await runs.get_global_setting()
        assert setting is not None
        await db.commit()
        await runs.set_global_enabled(enabled=True, actor="admin:test", expected_version=1)
        task = {
            "Id": 93003,
            "StatusId": 31,
            "ExecutorId": 10001,
            "CreatorId": 501,
            "Name": "Подключить принтер",
        }
        registration = await runs.register_assignment(
            task=task, assistant_user_id=10001, open_status_id=31
        )
        assert registration.run is not None
        run = registration.run
        runner = TicketRunRunner(db)

        await runner.advance(run_id=run.id, task=task)
        clarification = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "apply_triage",
            )
        )
        assert clarification is not None
        await CommandService(db).approve(
            clarification.id, decision="approve", reason=None, operator="operator:test"
        )
        claim = await CommandService(db).claim(
            clarification.id, worker_id="test-backend"
        )
        await CommandService(db).finish(
            clarification.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"results": [{"update_ok": True}]},
            error_message=None,
            worker_id="test-backend",
        )
        waiting = await runner.advance(run_id=run.id, task=task)
        assert waiting.state == "waiting_answer"
        assert waiting.clarification_count == 1
        waiting.waiting_until = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await db.commit()

        await runner.advance(run_id=run.id, task=task, comments=[])
        cancellation = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.idempotency_key.like("%:cancel_timeout"),
            )
        )
        assert cancellation is not None
        assert cancellation.status == "awaiting_approval"
        assert cancellation.params_json["status_id"] == 30
        assert cancellation.params_json["template_key"] == "ticket_timeout_cancel"


@pytest.mark.asyncio
async def test_unsupported_assignment_prepares_template_cancellation():
    async with AsyncSessionLocal() as db:
        await seed_templates(db)
        runs = TicketRunService(db)
        setting = await runs.get_global_setting()
        assert setting is not None
        await db.commit()
        await runs.set_global_enabled(enabled=True, actor="admin:test", expected_version=1)
        task = {
            "Id": 93004,
            "StatusId": 31,
            "ExecutorId": 10001,
            "Name": "Не открывается Excel",
        }
        registration = await runs.register_assignment(
            task=task, assistant_user_id=10001, open_status_id=31
        )
        assert registration.run is not None

        await TicketRunRunner(db).advance(run_id=registration.run.id, task=task)
        cancellation = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == registration.run.id,
                CommandRecord.idempotency_key.like("%:cancel_unsupported"),
            )
        )
        assert cancellation is not None
        assert cancellation.params_json["status_id"] == 30
        assert cancellation.params_json["template_key"] == "autopilot_unsupported_cancel"


@pytest.mark.asyncio
async def test_pc_offline_waits_for_new_applicant_comment():
    async with AsyncSessionLocal() as db:
        await seed_templates(db)
        runs = TicketRunService(db)
        setting = await runs.get_global_setting()
        assert setting is not None
        await db.commit()
        await runs.set_global_enabled(enabled=True, actor="admin:test", expected_version=1)
        task = {**printer_task(93005), "CreatorId": 501}
        registration = await runs.register_assignment(
            task=task, assistant_user_id=10001, open_status_id=31
        )
        assert registration.run is not None
        run = registration.run
        runner = TicketRunRunner(db)

        await runner.advance(run_id=run.id, task=task)
        diagnose = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "diagnose_host",
            )
        )
        assert diagnose is not None
        claim = await CommandService(db).claim(diagnose.id, worker_id="test-windows")
        await CommandService(db).finish(
            diagnose.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"status": "success", "payload": {"diagnostics": {"is_online": False}}},
            error_message=None,
            worker_id="test-windows",
        )
        await runner.advance(run_id=run.id, task=task)
        clarification = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.idempotency_key.like("%:request_pc_online"),
            )
        )
        assert clarification is not None
        await CommandService(db).approve(
            clarification.id, decision="approve", reason=None, operator="operator:test"
        )
        claim = await CommandService(db).claim(
            clarification.id, worker_id="test-backend"
        )
        await CommandService(db).finish(
            clarification.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"results": [{"update_ok": True}]},
            error_message=None,
            worker_id="test-backend",
        )
        waiting = await runner.advance(run_id=run.id, task=task)
        assert waiting.state == "waiting_answer"
        assert waiting.waiting_reason == "pc_offline"
        comment_time = (waiting.updated_at + dt.timedelta(seconds=1)).isoformat()

        unchanged = await runner.advance(
            run_id=run.id,
            task=task,
            comments=[
                {"EditorId": 10001, "Created": comment_time, "Comment": "Служебный комментарий"}
            ],
        )
        assert unchanged.state == "waiting_answer"

        resumed = await runner.advance(
            run_id=run.id,
            task=task,
            comments=[{"EditorId": 501, "Created": comment_time, "Comment": "ПК включил"}],
        )
        assert resumed.state == "running"
        assert resumed.current_step == "validate_request"


@pytest.mark.asyncio
async def test_verified_install_failure_creates_template_cancellation():
    async with AsyncSessionLocal() as db:
        await seed_templates(db)
        runs = TicketRunService(db)
        setting = await runs.get_global_setting()
        assert setting is not None
        await db.commit()
        await runs.set_global_enabled(enabled=True, actor="admin:test", expected_version=1)
        task = printer_task(93006)
        registration = await runs.register_assignment(
            task=task, assistant_user_id=10001, open_status_id=31
        )
        assert registration.run is not None
        run = registration.run
        runner = TicketRunRunner(db)

        await runner.advance(run_id=run.id, task=task)
        diagnose = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "diagnose_host",
            )
        )
        assert diagnose is not None
        claim = await CommandService(db).claim(diagnose.id, worker_id="test-windows")
        await CommandService(db).finish(
            diagnose.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"status": "success", "payload": {"diagnostics": {"is_online": True}}},
            error_message=None,
            worker_id="test-windows",
        )
        await runner.advance(run_id=run.id, task=task)
        install = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "install_printer",
            )
        )
        assert install is not None
        await CommandService(db).approve(
            install.id, decision="approve", reason=None, operator="operator:test"
        )
        claim = await CommandService(db).claim(install.id, worker_id="test-windows")
        await CommandService(db).finish(
            install.id,
            claim_token=claim.token,
            outcome="failed",
            result={
                "failure_kind": "verified_failure",
                "failure_code": "printer_not_found_after_install",
                "verified_failure": True,
            },
            error_message="Принтер не появился после установки",
            worker_id="test-windows",
        )

        await runner.advance(run_id=run.id, task=task)
        cancellation = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.idempotency_key.like("%:cancel_execution_failed"),
            )
        )
        assert cancellation is not None
        assert cancellation.params_json["template_key"] == "autopilot_execution_failed_cancel"
        assert cancellation.params_json["status_id"] == 30
