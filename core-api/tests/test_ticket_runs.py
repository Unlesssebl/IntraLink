import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.database.db import (
    AsyncSessionLocal,
    AutopilotScenario,
    AutopilotSettingEvent,
    CommandRecord,
    SystemSetting,
    TicketRun,
    TicketRunEvent,
    TriageTemplate,
)
from app.services.ticket_runs import REQUIRED_AUTOPILOT_TEMPLATES, TicketRunService
from app.services.command_service import CommandService
from app.services.decision_journal import DecisionJournalService


def assistant_task(task_id: int = 91001) -> dict:
    return {
        "Id": task_id,
        "StatusId": 31,
        "ExecutorId": 10001,
        "ExecutorIds": "10001,42",
        "ServiceId": 19,
    }


async def seed_autopilot_templates(db) -> None:
    status_by_key = {
        "printer_ip_clarify": 35,
        "pc_offline": 35,
        "ticket_timeout_cancel": 30,
        "ticket_not_relevant": 30,
        "wrong_service": 30,
        "autopilot_unsupported_cancel": 30,
        "autopilot_execution_failed_cancel": 30,
        "resolved_standard": 29,
    }
    for key in REQUIRED_AUTOPILOT_TEMPLATES:
        existing = await db.scalar(select(TriageTemplate).where(TriageTemplate.key == key))
        if existing:
            existing.status_id = status_by_key[key]
            existing.is_active = True
            continue
        db.add(
            TriageTemplate(
                key=key,
                name=key,
                category="autopilot",
                status_id=status_by_key[key],
                status_name="Шаблон",
                expenses=5,
                template_text="Шаблон",
                is_active=True,
            )
        )
    await db.commit()


async def seed_autopilot_prerequisites(db) -> None:
    await seed_autopilot_templates(db)
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


async def enable_autopilot(db) -> TicketRunService:
    await seed_autopilot_prerequisites(db)
    service = TicketRunService(db)
    setting = await service.get_global_setting()
    assert setting is not None
    await db.commit()
    await service.set_global_enabled(enabled=True, actor="admin:test", expected_version=1)
    return service


async def operational_decision(db, run, action: str, target: dict, parameters: dict):
    return await DecisionJournalService(db).record_operational(
        task_id=run.task_id,
        ticket_run_id=run.id,
        action=action,
        target=target,
        parameters=parameters,
        actor="test",
    )


@pytest.mark.asyncio
async def test_assignment_is_not_registered_while_global_gate_is_off():
    async with AsyncSessionLocal() as db:
        result = await TicketRunService(db).register_assignment(
            task=assistant_task(),
            assistant_user_id=10001,
            open_status_id=31,
        )

        assert result.created is False
        assert result.run is None
        assert result.reason == "autopilot_disabled"

    async with AsyncSessionLocal() as db:
        persisted = await db.scalar(select(TicketRun).where(TicketRun.task_id == 91001))
        assert persisted is None
        event_count = await db.scalar(select(func.count()).select_from(TicketRunEvent))
        assert event_count == 0


@pytest.mark.asyncio
async def test_repeated_poll_and_new_basis_do_not_create_second_active_run():
    async with AsyncSessionLocal() as db:
        service = await enable_autopilot(db)
        first = await service.register_assignment(
            task=assistant_task(91002),
            assistant_user_id=10001,
            open_status_id=31,
        )
        repeated = await service.register_assignment(
            task=assistant_task(91002),
            assistant_user_id=10001,
            open_status_id=31,
        )
        second_basis = await service.register_assignment(
            task=assistant_task(91002),
            assistant_user_id=10001,
            open_status_id=31,
            trigger_key="assignment-event:200",
            trigger_kind="assignment_event",
        )

        assert first.created is True
        assert repeated.created is False
        assert repeated.reason == "duplicate_trigger"
        assert second_basis.created is False
        assert second_basis.reason == "active_run_exists"
        count = await db.scalar(
            select(func.count()).select_from(TicketRun).where(TicketRun.task_id == 91002)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_reopen_does_not_reuse_initial_assignment_basis():
    async with AsyncSessionLocal() as db:
        service = await enable_autopilot(db)
        first = await service.register_assignment(
            task=assistant_task(91003),
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert first.run is not None
        first.run.state = "completed"
        first.run.outcome = "cancelled"
        from datetime import datetime, timezone

        first.run.completed_at = datetime.now(timezone.utc)
        await db.commit()

        reopened = await service.register_assignment(
            task=assistant_task(91003),
            assistant_user_id=10001,
            open_status_id=31,
        )
        explicit = await service.register_assignment(
            task=assistant_task(91003),
            assistant_user_id=10001,
            open_status_id=31,
            trigger_key=f"explicit:{uuid.uuid4()}",
            trigger_kind="explicit_start",
            actor="operator:test",
        )

        assert reopened.created is False
        assert reopened.reason == "duplicate_trigger"
        assert explicit.created is True


@pytest.mark.asyncio
async def test_global_gate_defaults_off_and_changes_are_audited():
    async with AsyncSessionLocal() as db:
        service = TicketRunService(db)
        setting = await service.get_global_setting()
        assert setting is not None
        assert setting.enabled is False
        assert setting.version == 1
        await db.commit()

    async with AsyncSessionLocal() as db:
        await seed_autopilot_prerequisites(db)
        updated = await TicketRunService(db).set_global_enabled(
            enabled=True,
            actor="admin:test",
            reason="acceptance",
            expected_version=1,
        )
        assert updated.enabled is True
        assert updated.version == 2
        events = list(
            (
                await db.scalars(
                    select(AutopilotSettingEvent).order_by(AutopilotSettingEvent.version)
                )
            ).all()
        )
        assert [(event.enabled, event.version) for event in events] == [
            (False, 1),
            (True, 2),
        ]


@pytest.mark.asyncio
async def test_disabled_assignment_is_not_retroactively_started():
    async with AsyncSessionLocal() as db:
        await seed_autopilot_prerequisites(db)
        service = TicketRunService(db)
        first = await service.register_assignment(
            task=assistant_task(91006),
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert first.run is None
        assert first.reason == "autopilot_disabled"
        await service.set_global_enabled(enabled=True, actor="admin:test", expected_version=1)

        wrong_status = await service.register_assignment(
            task={**assistant_task(91006), "StatusId": 35},
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert wrong_status.reason == "status_not_open"

        revalidated = await service.register_assignment(
            task=assistant_task(91006),
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert revalidated.created is True
        assert revalidated.reason == "created"
        assert revalidated.run is not None
        assert revalidated.run.state == "running"
        assert revalidated.run.pause_reason is None


@pytest.mark.asyncio
async def test_registration_rejects_wrong_status_and_executor():
    async with AsyncSessionLocal() as db:
        service = TicketRunService(db)
        closed = await service.register_assignment(
            task={**assistant_task(91004), "StatusId": 29},
            assistant_user_id=10001,
            open_status_id=31,
        )
        unassigned = await service.register_assignment(
            task={**assistant_task(91005), "ExecutorId": 42, "ExecutorIds": "42"},
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert closed.reason == "status_not_open"
        assert unassigned.reason == "assistant_not_assigned"


@pytest.mark.asyncio
async def test_command_can_be_durably_linked_to_ticket_run():
    async with AsyncSessionLocal() as db:
        run_service = await enable_autopilot(db)
        registration = await run_service.register_assignment(
            task=assistant_task(91007),
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert registration.run is not None
        target = {"host": "PC-91007", "task_id": 91007}
        decision = await operational_decision(db, registration.run, "diagnose_host", target, {})
        command, duplicate = await CommandService(db).create(
            action="diagnose_host",
            target=target,
            parameters={},
            idempotency_key="ticket-run-test-91007",
            initiator="autopilot",
            source="web",
            priority=5,
            ticket_run_id=registration.run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )
        assert duplicate is False
        assert command.ticket_run_id == registration.run.id

    async with AsyncSessionLocal() as db:
        persisted = await db.scalar(
            select(CommandRecord).where(CommandRecord.idempotency_key == "ticket-run-test-91007")
        )
        assert persisted is not None
        assert persisted.ticket_run_id == registration.run.id


@pytest.mark.asyncio
async def test_global_disable_pauses_run_and_cancels_unstarted_command():
    async with AsyncSessionLocal() as db:
        run_service = await enable_autopilot(db)
        registration = await run_service.register_assignment(
            task=assistant_task(91008),
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert registration.run is not None
        target = {"host": "PC-91008", "task_id": 91008}
        decision = await operational_decision(db, registration.run, "diagnose_host", target, {})
        command, _ = await CommandService(db).create(
            action="diagnose_host",
            target=target,
            parameters={},
            idempotency_key="ticket-run-disable-91008",
            initiator="autopilot",
            source="web",
            priority=5,
            ticket_run_id=registration.run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )

        await run_service.set_global_enabled(
            enabled=False, actor="admin:test", expected_version=2
        )
        await db.refresh(registration.run)
        await db.refresh(command)
        assert registration.run.state == "paused"
        assert registration.run.pause_reason == "global_disabled"
        assert command.status == "cancelled"
        assert command.error_message == "global_autopilot_disabled"


@pytest.mark.asyncio
async def test_successful_manual_final_status_completes_linked_run():
    async with AsyncSessionLocal() as db:
        run = await TicketRunService(db).start_manual(
            task_id=91009,
            status_id=31,
            allowed_status_ids={31},
            actor="operator:test",
            trigger_key="manual:91009:test",
        )
        service = CommandService(db)
        target = {"task_id": 91009}
        parameters = {
            "task_ids": [91009],
            "status_id": 29,
            "comment": "Выполнено",
            "expenses": 10,
        }
        decision = await operational_decision(db, run, "apply_triage", target, parameters)
        command, _ = await service.create(
            action="apply_triage",
            target=target,
            parameters=parameters,
            idempotency_key="manual-finalize-91009",
            initiator="operator:test",
            source="web",
            priority=5,
            ticket_run_id=run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )
        if command.status == "awaiting_approval":
            command = await service.approve(
                command.id,
                decision="approve",
                reason=None,
                operator="operator:test",
            )
        claim = await service.claim(command.id, worker_id="backend:test")
        await service.finish(
            command.id,
            claim_token=claim.token,
            outcome="succeeded",
            result={"results": [{"task_id": 91009, "update_ok": True}]},
            error_message=None,
            worker_id="backend:test",
        )

        await db.refresh(run)
        assert run.state == "completed"
        assert run.outcome == "completed"
        assert run.completed_at is not None
        event = await db.scalar(
            select(TicketRunEvent)
            .where(
                TicketRunEvent.ticket_run_id == run.id,
                TicketRunEvent.event_type == "run_completed",
            )
            .order_by(TicketRunEvent.sequence.desc())
        )
        assert event is not None
        assert event.details_json["command_id"] == str(command.id)


@pytest.mark.asyncio
async def test_globally_paused_run_requires_explicit_resume():
    async with AsyncSessionLocal() as db:
        service = await enable_autopilot(db)
        created = await service.register_assignment(
            task=assistant_task(91010),
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert created.run is not None
        await service.set_global_enabled(
            enabled=False, actor="admin:test", expected_version=2
        )
        await service.set_global_enabled(
            enabled=True, actor="admin:test", expected_version=3
        )

        resumed = await service.register_assignment(
            task=assistant_task(91010),
            assistant_user_id=10001,
            open_status_id=31,
        )
        assert resumed.created is False
        assert resumed.reason == "duplicate_trigger"
        assert resumed.run is not None
        assert resumed.run.state == "paused"
        assert resumed.run.pause_reason == "global_disabled"


@pytest.mark.asyncio
async def test_manual_write_waits_for_started_operation_to_finish():
    async with AsyncSessionLocal() as db:
        run = await TicketRunService(db).start_manual(
            task_id=91011,
            status_id=31,
            allowed_status_ids={31},
            actor="operator:test",
            trigger_key="manual:91011:test",
        )
        service = CommandService(db)
        target = {"task_id": 91011, "host": "PC-91011"}
        decision = await operational_decision(db, run, "diagnose_host", target, {})
        diagnosis, _ = await service.create(
            action="diagnose_host",
            target=target,
            parameters={},
            idempotency_key="manual-running-91011",
            initiator="operator:test",
            source="web",
            priority=5,
            ticket_run_id=run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )
        await service.claim(diagnosis.id, worker_id="windows:test")

        with pytest.raises(HTTPException) as exc:
            await service.create(
                action="apply_triage",
                target={"task_id": 91011},
                parameters={"task_ids": [91011], "status_id": 27},
                idempotency_key="manual-conflict-91011",
                initiator="operator:test",
                source="web",
                priority=5,
                ticket_run_id=run.id,
            )
        assert exc.value.status_code == 409
        assert "operation in progress" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_global_enable_rejects_active_but_misconfigured_template():
    async with AsyncSessionLocal() as db:
        await seed_autopilot_prerequisites(db)
        template = await db.scalar(
            select(TriageTemplate).where(TriageTemplate.key == "resolved_standard")
        )
        assert template is not None
        template.status_id = 30
        service = TicketRunService(db)
        setting = await service.get_global_setting()
        assert setting is not None
        await db.commit()

        with pytest.raises(ValueError) as exc:
            await service.set_global_enabled(
                enabled=True,
                actor="admin:test",
                expected_version=1,
            )
        assert "resolved_standard" in str(exc.value)


@pytest.mark.asyncio
async def test_active_runs_are_pageable_without_duplicates():
    async with AsyncSessionLocal() as db:
        service = TicketRunService(db)
        for task_id in range(91100, 91107):
            await service.start_manual(
                task_id=task_id,
                status_id=31,
                allowed_status_ids={31},
                actor="operator:test",
                trigger_key=f"manual:{task_id}:paging",
            )

        seen = []
        after_id = None
        while True:
            page = await service.list_active(limit=2, after_id=after_id)
            if not page:
                break
            seen.extend(run.id for run in page)
            after_id = page[-1].id

        assert len(seen) == 7
        assert len(set(seen)) == 7


@pytest.mark.asyncio
async def test_v2_command_cannot_bypass_active_ticket_run_link():
    async with AsyncSessionLocal() as db:
        await TicketRunService(db).start_manual(
            task_id=91110,
            status_id=31,
            allowed_status_ids={31},
            actor="operator:test",
            trigger_key="manual:91110:guard",
        )

        with pytest.raises(HTTPException) as exc:
            await CommandService(db).create(
                action="diagnose_host",
                target={"task_id": 91110, "host": "PC-91110"},
                parameters={},
                idempotency_key="unlinked-active-run-91110",
                initiator="operator:test",
                source="web",
                priority=5,
            )

        assert exc.value.status_code == 409
        assert "must be linked" in str(exc.value.detail)
