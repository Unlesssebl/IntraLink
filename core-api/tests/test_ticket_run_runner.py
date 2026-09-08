import pytest
import datetime as dt
from unittest.mock import patch
from sqlalchemy import select

from app.database.db import (
    AsyncSessionLocal, AutopilotScenario, CommandRecord, SystemSetting, TicketRunEvent, TriageTemplate,
)
from app.services.command_service import CommandService
from app.services.ticket_run_runner import TicketRunRunner
from app.services.ticket_runs import REQUIRED_AUTOPILOT_TEMPLATES, TicketRunService


async def seed_templates(db) -> None:
    statuses = {
        "printer_ip_clarify": 35,
        "pc_offline": 35,
        "ticket_timeout_cancel": 30,
        "ticket_not_relevant": 30,
        "wrong_service": 30,
        "autopilot_unsupported_cancel": 30,
        "autopilot_execution_failed_cancel": 30,
        "resolved_standard": 29,
    }
    texts = {
        "pc_offline": "ПК {pc_name} недоступен.",
        "wrong_service": "Оставьте заявку в разделе: {target_service}",
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


async def seed_autopilot_prerequisites(db) -> None:
    await seed_templates(db)
    db.add(SystemSetting(
        key="service_account_config",
        value_json={"login": "assistant", "encrypted_password": "test", "user_id": 10001},
        is_encrypted=True,
    ))
    db.add(AutopilotScenario(
        service_id=19,
        scenario_key="printer_installation",
        enabled=True,
        config_json={},
        updated_by="test",
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_redirect_requires_configured_target_link():
    async with AsyncSessionLocal() as db:
        runs = await enable_autopilot(db)
        registration = await runs.register_assignment(
            task=printer_task(93010), assistant_user_id=10001, open_status_id=31
        )
        assert registration.run is not None
        redirect = {
            "is_redirect": True,
            "target_root": "04",
            "target_service_name": "Служба поддержки",
            "reason": "wrong_section",
        }
        with patch("app.services.ticket_run_runner.detect_service_redirect", return_value=redirect):
            run = await TicketRunRunner(db).advance(
                run_id=registration.run.id, task=printer_task(93010)
            )
        assert run.state == "paused"
        assert run.pause_reason == "redirect_target_unavailable"
        assert await db.scalar(
            select(CommandRecord).where(CommandRecord.ticket_run_id == run.id)
        ) is None


@pytest.mark.asyncio
async def test_unambiguous_redirect_with_link_uses_dedicated_template():
    async with AsyncSessionLocal() as db:
        runs = await enable_autopilot(db)
        scenario = await db.scalar(
            select(AutopilotScenario).where(AutopilotScenario.service_id == 19)
        )
        scenario.config_json = {
            "redirect_targets": {
                "04": {"name": "Служба поддержки", "url": "https://helpdesk.example/service/4"}
            }
        }
        await db.commit()
        registration = await runs.register_assignment(
            task=printer_task(93011), assistant_user_id=10001, open_status_id=31
        )
        redirect = {
            "is_redirect": True,
            "target_root": "04",
            "target_service_name": "Служба поддержки",
            "reason": "wrong_section",
        }
        with patch("app.services.ticket_run_runner.detect_service_redirect", return_value=redirect):
            await TicketRunRunner(db).advance(
                run_id=registration.run.id, task=printer_task(93011)
            )
        command = await db.scalar(
            select(CommandRecord).where(CommandRecord.ticket_run_id == registration.run.id)
        )
        assert command is not None
        assert command.params_json["template_key"] == "wrong_service"
        assert "https://helpdesk.example/service/4" in command.params_json["comment"]


async def enable_autopilot(db) -> TicketRunService:
    await seed_autopilot_prerequisites(db)
    runs = TicketRunService(db)
    setting = await runs.get_global_setting()
    assert setting is not None
    await db.commit()
    await runs.set_global_enabled(enabled=True, actor="admin:test", expected_version=1)
    return runs


def printer_task(task_id: int) -> dict:
    return {
        "Id": task_id,
        "StatusId": 31,
        "ExecutorId": 10001,
        "ServiceId": 19,
        "Name": "HP LaserJet 9100",
        "CustomFields": [
            {"CustomFieldId": 1112, "Value": "PC-93001"},
            {"CustomFieldId": 1103, "Value": "10.20.30.40"},
        ],
    }


@pytest.mark.asyncio
async def test_printer_happy_path_uses_only_linked_v2_commands():
    async with AsyncSessionLocal() as db:
        runs = await enable_autopilot(db)
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
        await seed_autopilot_prerequisites(db)
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
            "ServiceId": 19,
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
        runs = await enable_autopilot(db)
        task = {
            "Id": 93003,
            "StatusId": 31,
            "ExecutorId": 10001,
            "CreatorId": 501,
            "Name": "Подключить принтер",
            "ServiceId": 19,
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
async def test_unsupported_assignment_pauses_without_ticket_mutation():
    async with AsyncSessionLocal() as db:
        runs = await enable_autopilot(db)
        task = {
            "Id": 93004,
            "StatusId": 31,
            "ExecutorId": 10001,
            "ServiceId": 19,
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
        await db.refresh(registration.run)
        assert cancellation is None
        assert registration.run.state == "paused"
        assert registration.run.pause_reason == "unsupported_scenario"


@pytest.mark.asyncio
async def test_pc_offline_waits_for_new_applicant_comment():
    async with AsyncSessionLocal() as db:
        runs = await enable_autopilot(db)
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
async def test_verified_install_failure_pauses_without_cancellation():
    async with AsyncSessionLocal() as db:
        runs = await enable_autopilot(db)
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
        await db.refresh(run)
        assert cancellation is None
        assert run.state == "paused"
        assert run.pause_reason == "execution_failed"


@pytest.mark.asyncio
async def test_pre_cancellation_guard_aborts_on_applicant_reply():
    async with AsyncSessionLocal() as db:
        runs = await enable_autopilot(db)
        task = {
            "Id": 93007,
            "StatusId": 31,
            "ExecutorId": 10001,
            "CreatorId": 501,
            "Name": "Подключить принтер",
            "ServiceId": 19,
        }
        registration = await runs.register_assignment(
            task=task, assistant_user_id=10001, open_status_id=31
        )
        assert registration.run is not None
        run = registration.run
        runner = TicketRunRunner(db)

        # 1. Start run and issue clarification
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
        claim = await CommandService(db).claim(clarification.id, worker_id="test-backend")
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

        # Simulate timeout expired
        now = dt.datetime.now(dt.timezone.utc)
        waiting.waiting_until = now - dt.timedelta(seconds=1)
        await db.commit()

        # Mock fresh task and fresh comments from applicant
        fresh_task = {
            **task,
            "StatusId": 35,
            "CustomFields": [
                {"CustomFieldId": 1112, "Value": "PC-93007"},
                {"CustomFieldId": 1103, "Value": "10.20.30.77"},
            ],
        }
        fresh_comments = [
            {
                "EditorId": 501,
                "Created": (now - dt.timedelta(minutes=5)).isoformat(),
                "Comment": "IP принтера 10.20.30.77, ПК PC-93007",
            }
        ]

        with patch("app.services.worker.get_single_task", return_value=fresh_task), \
             patch("app.services.worker.get_task_comments", return_value=fresh_comments):
            resumed = await runner.advance(
                run_id=run.id,
                task=task,
                service_auth_b64="test-service-auth",
            )

        # Verify cancellation was aborted and no cancel command exists
        cancellation = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.idempotency_key.like("%:cancel_timeout"),
            )
        )
        assert cancellation is None
        assert resumed.state != "waiting_answer"
        assert resumed.waiting_reason is None
        assert resumed.waiting_until is None

        # Verify event details recorded cancellation_aborted
        events = list(
            (
                await db.scalars(
                    select(TicketRunEvent)
                    .where(TicketRunEvent.ticket_run_id == run.id)
                    .order_by(TicketRunEvent.sequence)
                )
            ).all()
        )
        abort_events = [e for e in events if (e.details_json or {}).get("cancellation_aborted")]
        assert len(abort_events) >= 1
        assert abort_events[0].details_json["reason"] == "applicant_replied"


@pytest.mark.asyncio
async def test_pre_cancellation_guard_completes_on_external_close():
    async with AsyncSessionLocal() as db:
        runs = await enable_autopilot(db)
        task = {
            "Id": 93008,
            "StatusId": 31,
            "ExecutorId": 10001,
            "CreatorId": 501,
            "Name": "Подключить принтер",
            "ServiceId": 19,
        }
        registration = await runs.register_assignment(
            task=task, assistant_user_id=10001, open_status_id=31
        )
        run = registration.run
        runner = TicketRunRunner(db)

        # Move to waiting_answer
        await runner.advance(run_id=run.id, task=task)
        clarification = await db.scalar(
            select(CommandRecord).where(CommandRecord.ticket_run_id == run.id)
        )
        await CommandService(db).approve(clarification.id, decision="approve", reason=None, operator="operator:test")
        claim = await CommandService(db).claim(clarification.id, worker_id="test-backend")
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

        waiting.waiting_until = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await db.commit()

        # Mock fresh task indicating ticket was completed externally (StatusId: 29)
        with patch("app.services.worker.get_single_task", return_value={"StatusId": 29, "Id": 93008}), \
             patch("app.services.worker.get_task_comments", return_value=[]):
            finished = await runner.advance(
                run_id=run.id,
                task=task,
                service_auth_b64="test-service-auth",
            )

        cancellation = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.idempotency_key.like("%:cancel_timeout"),
            )
        )
        assert cancellation is None
        assert finished.state == "completed"

