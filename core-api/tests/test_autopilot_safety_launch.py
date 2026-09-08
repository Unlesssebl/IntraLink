"""Stage 3 comprehensive automated test suite for safe autopilot launch of account creation.

Covers all 10 requirements:
1. Poller passes ExecutorIds="10502" and iterates through multiple pages.
2. Unchanged ticket does not block reconciliation of new command version/status.
3. failed, rejected, cancelled, and needs_review produce safe publication and correct TicketRun state.
4. resolution_error publishes without command_id.
5. Repeated reconciliation does not create duplicate comments (idempotency).
6. Post-external crash recovery via stable marker in IntraService history.
7. IntraService unavailability records delivery failure and permits retry.
8. Public comment does not leak command.error_message, stack traces, or credentials.
9. needs_review does not create a repeated create_user command.
10. Missing each required fact leads to request_clarification and status 35.
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.db import (
    AsyncSessionLocal,
    AutopilotScenario,
    CommandEvent,
    CommandRecord,
    ResolutionPolicy,
    ResponseTemplate,
    TicketRun,
    TicketRunEvent,
)
from app.services.command_delivery import CommandDeliveryService, sanitize_error_code
from app.services.scenario_orchestrator import TicketRunOrchestrator, ticket_event_key
from app.services.ticket_run_runner import TicketRunRunner
from app.services.ticket_runs import TicketRunService, TicketRunState
from app.services.worker import process_autonomous_lifecycle


async def _seed_test_policies(db):
    templates = [
        ResponseTemplate(
            key="account_details_clarify",
            version=1,
            name="Уточнение реквизитов",
            template_text="Уточните реквизиты сотрудника: {{ invalid_fields }}.",
            required_variables=[],
            is_active=True,
            created_by="test",
        ),
        ResponseTemplate(
            key="create_user_proposed",
            version=1,
            name="Создание учётной записи",
            template_text="Создание учётной записи.",
            required_variables=[],
            is_active=True,
            created_by="test",
        ),
        ResponseTemplate(
            key="user_created",
            version=1,
            name="Учётная запись создана",
            template_text="Учётная запись создана. Логин: {{ login }}",
            required_variables=["login"],
            is_active=True,
            created_by="test",
        ),
    ]
    for tmpl in templates:
        existing = await db.scalar(
            select(ResponseTemplate).where(
                ResponseTemplate.key == tmpl.key,
                ResponseTemplate.version == tmpl.version,
            )
        )
        if not existing:
            db.add(tmpl)
    await db.flush()

    tmpl_map = {}
    for t in (await db.scalars(select(ResponseTemplate))).all():
        tmpl_map[t.key] = t.id

    policies = [
        ResolutionPolicy(
            outcome_key="account_details_invalid",
            version=1,
            outcome_kind="clarification",
            template_id=tmpl_map["account_details_clarify"],
            target_status_id=35,
            status_name="Требует уточнения",
            expenses=5,
            action_id=None,
            risk_level=0,
            requires_approval=False,
            is_active=True,
            created_by="test",
        ),
        ResolutionPolicy(
            outcome_key="create_user_proposed",
            version=1,
            outcome_kind="action",
            template_id=tmpl_map["create_user_proposed"],
            target_status_id=None,
            status_name=None,
            expenses=10,
            action_id="create_user",
            risk_level=2,
            requires_approval=True,
            is_active=True,
            created_by="test",
        ),
        ResolutionPolicy(
            outcome_key="user_created",
            version=1,
            outcome_kind="resolution",
            template_id=tmpl_map["user_created"],
            target_status_id=29,
            status_name="Решена",
            expenses=10,
            action_id=None,
            risk_level=0,
            requires_approval=False,
            is_active=True,
            created_by="test",
        ),
    ]
    for pol in policies:
        existing = await db.scalar(
            select(ResolutionPolicy).where(
                ResolutionPolicy.outcome_key == pol.outcome_key,
                ResolutionPolicy.version == pol.version,
            )
        )
        if not existing:
            db.add(pol)
    await db.commit()


async def _create_test_run(db, task_id: int, scenario_key: str = "create_user") -> TicketRun:
    run = TicketRun(
        id=uuid.uuid4(),
        task_id=task_id,
        mode="autopilot",
        state=TicketRunState.RUNNING.value,
        trigger_kind="ticket_created",
        trigger_key=f"ticket:{task_id}:created",
        trigger_snapshot_json={"scenario_key": scenario_key},
        scenario_key=scenario_key,
        scenario_version=1,
        fact_revision=0,
        version=1,
        created_by="test",
        updated_by="test",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


# ==============================================================================
# Requirement 1: Poller passes ExecutorIds="10502" and iterates through pages
# ==============================================================================
@pytest.mark.asyncio
async def test_poller_executor_ids_and_pagination():
    assistant_id = 10502
    page1_tasks = [{"Id": i, "StatusId": 31, "ExecutorIds": str(assistant_id)} for i in range(1, 101)]
    page2_tasks = [{"Id": i, "StatusId": 31, "ExecutorIds": str(assistant_id)} for i in range(101, 121)]

    calls = []

    async def mock_get_tasks(auth_b64, filters=None):
        calls.append(filters)
        page = str((filters or {}).get("page") or "1")
        if page == "1":
            return {"Tasks": page1_tasks}
        elif page == "2":
            return {"Tasks": page2_tasks}
        return {"Tasks": []}

    with (
        patch("app.services.vault.get_service_account_user_id", new=AsyncMock(return_value=assistant_id)),
        patch("app.services.worker.get_tasks", new=mock_get_tasks),
        patch("app.services.ticket_runs.register_observed_assignments", new=AsyncMock(return_value=[])) as mock_register,
        patch("app.services.ticket_runs.TicketRunService.list_active", new=AsyncMock(return_value=[])),
    ):
        await process_autonomous_lifecycle("mock-auth")

    # Проверяем, что передавался именно ExecutorIds (строка)
    assert len(calls) == 2
    assert calls[0]["ExecutorIds"] == "10502"
    assert "ExecutorId" not in calls[0]
    assert calls[0]["page"] == "1"
    assert calls[1]["page"] == "2"

    # Проверяем, что register_observed_assignments получил все 120 задач
    assert mock_register.await_count == 1
    passed_tasks = mock_register.await_args.kwargs["tasks"]
    assert len(passed_tasks) == 120


# ==============================================================================
# Requirement 2: Unchanged ticket does not block reconciliation of new command
# ==============================================================================
@pytest.mark.asyncio
async def test_unchanged_ticket_reconciles_command_status():
    async with AsyncSessionLocal() as db:
        await _seed_test_policies(db)
        run = await _create_test_run(db, task_id=141002)

        task_data = {
            "Id": 141002,
            "ServiceId": 53,
            "Name": "Создать пользователя",
            "Description": "Иванов Иван",
        }
        comments: list[dict] = []

        orchestrator = TicketRunOrchestrator(db)

        # Создаем команду вручную в статусе running
        command = CommandRecord(
            idempotency_key=f"cmd-test-{uuid.uuid4()}",
            request_hash="a" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": 141002},
            params_json={"surname": "Иванов"},
            status="running",
            version=1,
            ticket_run_id=run.id,
            initiator="test",
            source="test",
        )
        db.add(command)
        await db.commit()
        await db.refresh(command)

        # 1. Первая обработка: событие заявки зафиксировано
        initial_key = ticket_event_key(task_data, comments, "ticket_changed")
        await orchestrator.runs._append_run_event(
            run,
            event_type="ticket_changed",
            event_key=initial_key,
            actor="test",
            details={},
        )
        await db.commit()

        # 2. Команда завершилась в фоне с failed
        command.status = "failed"
        command.version = 2
        command.error_message = "AD server timeout"
        await db.commit()

        # 3. Вызываем advance с АБСОЛЮТНО НЕИЗМЕННЫМ task_data и comments
        with patch("app.services.command_delivery.CommandDeliveryService.deliver_command_failure", new=AsyncMock(return_value={"status": "delivered"})):
            result = await orchestrator.advance(
                run_id=run.id,
                task=task_data,
                comments=comments,
                event_type="ticket_changed",
                actor="reconciler",
                service_auth_b64="test-auth",
            )

        # Не должно вернуть duplicate_event=True, а должно обработать новый статус команды!
        assert not result.duplicate_event
        assert result.command is not None
        assert result.command.status == "failed"
        assert result.run.state == TicketRunState.PAUSED.value
        assert result.run.pause_reason == "command_failed"


# ==============================================================================
# Requirement 3 & 8: Terminal failure publication, safe comment, no secrets
# ==============================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("status_val", ["failed", "rejected", "cancelled", "needs_review"])
async def test_command_terminal_failures_safe_publication(status_val):
    async with AsyncSessionLocal() as db:
        await _seed_test_policies(db)
        run = await _create_test_run(db, task_id=141003 + hash(status_val) % 1000)

        # В error_message присутствуют секретные данные
        secret_leak = "Password=SuperSecret123! server=dc1.corp.loc:389 Traceback (most recent call last):"
        command = CommandRecord(
            idempotency_key=f"cmd-fail-{status_val}-{uuid.uuid4()}",
            request_hash="b" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": run.task_id},
            params_json={"surname": "Петров"},
            status=status_val,
            version=1,
            ticket_run_id=run.id,
            error_message=secret_leak,
            initiator="test",
            source="test",
        )
        db.add(command)
        await db.commit()
        await db.refresh(command)

        delivery = CommandDeliveryService(db)

        with (
            patch("app.services.intraservice.get_task_lifetime", new=AsyncMock(return_value=[])),
            patch("app.services.intraservice.get_single_task", new=AsyncMock(return_value={"Id": run.task_id, "StatusId": 31})),
            patch("app.services.intraservice.update_task_full", new=AsyncMock(return_value=True)) as mock_update,
        ):
            res = await delivery.deliver_command_failure(
                command.id,
                actor="test-actor",
                service_auth_b64="test-auth",
            )

        assert res["status"] == "delivered"
        mock_update.assert_awaited_once()
        call_kwargs = mock_update.await_args.kwargs
        assert call_kwargs["task_id"] == run.task_id
        assert call_kwargs["status_id"] == 27  # Оставлена в статусе 27 для инженера
        comment = call_kwargs["comment"]

        # Проверка требования 8: отсутствие секретов, паролей, stack trace
        assert "SuperSecret123!" not in comment
        assert "Password" not in comment
        assert "dc1.corp.loc" not in comment
        assert "Traceback" not in comment

        # Проверка текста
        if status_val == "needs_review":
            assert "Результат автоматического выполнения не удалось подтвердить" in comment
            assert "требуется проверить наличие учетной записи в Active Directory" in comment
        else:
            assert "При автоматической обработке заявки произошла ошибка" in comment
            assert "ожидает ручной проверки инженером" in comment

        # Проверка маркера
        assert f"[AUTOPILOT:{run.id}:cmd_{command.id}_{command.version}_{status_val}]" in comment


# ==============================================================================
# Requirement 4: resolution_error publishes without command_id
# ==============================================================================
@pytest.mark.asyncio
async def test_resolution_error_delivery_without_command():
    async with AsyncSessionLocal() as db:
        await _seed_test_policies(db)
        run = await _create_test_run(db, task_id=141004)

        delivery = CommandDeliveryService(db)

        with (
            patch("app.services.intraservice.get_task_lifetime", new=AsyncMock(return_value=[])),
            patch("app.services.intraservice.get_single_task", new=AsyncMock(return_value={"Id": run.task_id, "StatusId": 31})),
            patch("app.services.intraservice.update_task_full", new=AsyncMock(return_value=True)) as mock_update,
        ):
            res = await delivery.deliver_run_failure(
                run.id,
                error_code="resolution_policy_missing",
                error_message="Technical details: table policy row absent",
                actor="test-actor",
                service_auth_b64="test-auth",
            )

        assert res["status"] == "delivered"
        assert res["run_id"] == str(run.id)
        mock_update.assert_awaited_once()
        comment = mock_update.await_args.kwargs["comment"]
        assert "Код ошибки: resolution_policy_missing" in comment
        assert "Technical details" not in comment
        assert f"[AUTOPILOT:{run.id}:run_resolution_policy_missing_{run.version}]" in comment

        # Проверяем событие run_failure_delivery_succeeded
        ev = await db.scalar(
            select(TicketRunEvent).where(
                TicketRunEvent.ticket_run_id == run.id,
                TicketRunEvent.event_type == "run_failure_delivery_succeeded",
            )
        )
        assert ev is not None
        assert ev.details_json["safe_error_code"] == "resolution_policy_missing"


# ==============================================================================
# Requirement 5: Repeated reconciliation does not create second comment
# ==============================================================================
@pytest.mark.asyncio
async def test_reconciliation_delivery_idempotency():
    async with AsyncSessionLocal() as db:
        await _seed_test_policies(db)
        run = await _create_test_run(db, task_id=141005)

        command = CommandRecord(
            idempotency_key=f"cmd-idem-{uuid.uuid4()}",
            request_hash="c" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": run.task_id},
            params_json={"surname": "Сидоров"},
            status="failed",
            version=1,
            ticket_run_id=run.id,
            error_message="Some error",
            initiator="test",
            source="test",
        )
        db.add(command)
        await db.commit()

        delivery = CommandDeliveryService(db)

        # Первый вызов: доставка выполняется
        with (
            patch("app.services.intraservice.get_task_lifetime", new=AsyncMock(return_value=[])),
            patch("app.services.intraservice.get_single_task", new=AsyncMock(return_value={"Id": run.task_id, "StatusId": 31})),
            patch("app.services.intraservice.update_task_full", new=AsyncMock(return_value=True)) as mock_update,
        ):
            res1 = await delivery.deliver_command_failure(command.id, actor="test", service_auth_b64="test-auth")
            assert res1["status"] == "delivered"
            assert mock_update.await_count == 1

        # Второй вызов (reconciliation): не должно быть повторного update_task_full
        with (
            patch("app.services.intraservice.update_task_full", new=AsyncMock(return_value=True)) as mock_update2,
        ):
            res2 = await delivery.deliver_command_failure(command.id, actor="test", service_auth_b64="test-auth")
            assert res2["status"] == "already_delivered"
            mock_update2.assert_not_awaited()


# ==============================================================================
# Requirement 6: Crash recovery via stable marker in IntraService history
# ==============================================================================
@pytest.mark.asyncio
async def test_crash_recovery_via_stable_marker_in_history():
    async with AsyncSessionLocal() as db:
        await _seed_test_policies(db)
        run = await _create_test_run(db, task_id=141006)

        command = CommandRecord(
            idempotency_key=f"cmd-crash-{uuid.uuid4()}",
            request_hash="d" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": run.task_id},
            params_json={"surname": "Козлов"},
            status="failed",
            version=1,
            ticket_run_id=run.id,
            error_message="Timeout",
            initiator="test",
            source="test",
        )
        db.add(command)
        await db.commit()

        expected_marker = f"[AUTOPILOT:{run.id}:cmd_{command.id}_{command.version}_{command.status}]"
        # Имитируем историю IntraService, содержащую маркер (процесс упал до commit в локальную БД)
        remote_history = [
            {"Comment": f"При автоматической обработке заявки произошла ошибка...\n\n{expected_marker}"}
        ]

        delivery = CommandDeliveryService(db)

        with (
            patch("app.services.intraservice.get_task_lifetime", new=AsyncMock(return_value=remote_history)),
            patch("app.services.intraservice.get_single_task", new=AsyncMock(return_value={"Id": run.task_id})),
            patch("app.services.intraservice.update_task_full", new=AsyncMock(return_value=True)) as mock_update,
        ):
            res = await delivery.deliver_command_failure(command.id, actor="reconciler", service_auth_b64="test-auth")

        assert res["status"] == "delivered"
        # Внешний update_task_full НЕ вызывался повторно!
        mock_update.assert_not_awaited()

        # А локальное событие зафиксировано с флагом verified_by_history
        ev = await db.scalar(
            select(TicketRunEvent).where(
                TicketRunEvent.ticket_run_id == run.id,
                TicketRunEvent.event_type == "command_failure_delivery_succeeded",
            )
        )
        assert ev is not None
        assert ev.details_json["verified_by_history"] is True


# ==============================================================================
# Requirement 7: IntraService unavailability records failure & permits retry
# ==============================================================================
@pytest.mark.asyncio
async def test_intraservice_unavailability_retryable():
    async with AsyncSessionLocal() as db:
        await _seed_test_policies(db)
        run = await _create_test_run(db, task_id=141007)

        command = CommandRecord(
            idempotency_key=f"cmd-retry-{uuid.uuid4()}",
            request_hash="e" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": run.task_id},
            params_json={"surname": "Новиков"},
            status="failed",
            version=1,
            ticket_run_id=run.id,
            error_message="Network down",
            initiator="test",
            source="test",
        )
        db.add(command)
        await db.commit()

        delivery = CommandDeliveryService(db)

        # 1. Первый раз IntraService падает с ошибкой 502 / ConnectionError
        with (
            patch("app.services.intraservice.get_task_lifetime", new=AsyncMock(return_value=[])),
            patch("app.services.intraservice.get_single_task", new=AsyncMock(return_value={"Id": run.task_id})),
            patch("app.services.intraservice.update_task_full", new=AsyncMock(side_effect=RuntimeError("IntraService 502 Bad Gateway"))),
        ):
            with pytest.raises(Exception) as exc:
                await delivery.deliver_command_failure(command.id, actor="test", service_auth_b64="test-auth")
            assert "502" in str(exc.value) or "Bad Gateway" in str(exc.value)

        # Проверяем, что событие ошибки записалось
        failed_ev = await db.scalar(
            select(TicketRunEvent).where(
                TicketRunEvent.ticket_run_id == run.id,
                TicketRunEvent.event_type == "command_failure_delivery_failed",
            )
        )
        assert failed_ev is not None

        # 2. Повторная сверка poller через reconcile_undelivered_failure, когда IntraService восстановился
        with (
            patch("app.services.intraservice.get_task_lifetime", new=AsyncMock(return_value=[])),
            patch("app.services.intraservice.get_single_task", new=AsyncMock(return_value={"Id": run.task_id})),
            patch("app.services.intraservice.update_task_full", new=AsyncMock(return_value=True)) as mock_update,
        ):
            retried = await delivery.reconcile_undelivered_failure(run.id, actor="poller", service_auth_b64="test-auth")
            assert retried is True
            mock_update.assert_awaited_once()

        # Теперь есть событие успешной доставки
        success_ev = await db.scalar(
            select(TicketRunEvent).where(
                TicketRunEvent.ticket_run_id == run.id,
                TicketRunEvent.event_type == "command_failure_delivery_succeeded",
            )
        )
        assert success_ev is not None


# ==============================================================================
# Requirement 9: needs_review does not create a repeated create_user command
# ==============================================================================
@pytest.mark.asyncio
async def test_needs_review_does_not_retry_create_user():
    async with AsyncSessionLocal() as db:
        await _seed_test_policies(db)
        run = await _create_test_run(db, task_id=141009)

        task_data = {
            "Id": 141009,
            "ServiceId": 53,
            "Name": "Создать пользователя",
            "Description": "Иванов Иван",
        }

        command = CommandRecord(
            idempotency_key=f"cmd-review-{uuid.uuid4()}",
            request_hash="f" * 64,
            action="create_user",
            executor="windows",
            target_json={"task_id": run.task_id},
            params_json={"surname": "Иванов"},
            status="needs_review",
            version=1,
            ticket_run_id=run.id,
            error_message="Worker lost lease during AD mutation",
            initiator="test",
            source="test",
        )
        db.add(command)
        await db.commit()

        orchestrator = TicketRunOrchestrator(db)

        with patch("app.services.command_delivery.CommandDeliveryService.deliver_command_failure", new=AsyncMock(return_value={"status": "delivered"})):
            result = await orchestrator.advance(
                run_id=run.id,
                task=task_data,
                actor="test",
                service_auth_b64="test-auth",
            )

        assert result.run.state == TicketRunState.PAUSED.value
        assert result.run.pause_reason == "command_result_requires_review"

        # Проверяем количество команд: по-прежнему ровно 1 команда, никакой retry не создан!
        commands = (
            await db.scalars(
                select(CommandRecord).where(CommandRecord.ticket_run_id == run.id)
            )
        ).all()
        assert len(commands) == 1
        assert commands[0].id == command.id

        # Повторный запуск через runner не должен ничего менять
        runner = TicketRunRunner(db)
        run_res = await runner.advance(
            run_id=run.id,
            task=task_data,
            actor="poller",
            service_auth_b64="test-auth",
        )
        assert run_res.state == TicketRunState.PAUSED.value

        commands_after = (
            await db.scalars(
                select(CommandRecord).where(CommandRecord.ticket_run_id == run.id)
            )
        ).all()
        assert len(commands_after) == 1


# ==============================================================================
# Requirement 10: Missing each required fact leads to request_clarification & 35
# ==============================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field",
    ["surname", "name", "company", "department", "title"],
)
async def test_missing_required_facts_triggers_clarification(missing_field):
    async with AsyncSessionLocal() as db:
        await _seed_test_policies(db)
        run = await _create_test_run(db, task_id=141010 + hash(missing_field) % 1000)

        # Полный набор фактов
        all_facts = {
            "1057": "Иванов",       # surname
            "1058": "Иван",         # name
            "1074": "ИнтраЛаб",     # company / organization
            "1064": "Департамент ИТ", # department
            "1065": "Инженер",      # title
        }

        field_to_id = {
            "surname": "1057",
            "name": "1058",
            "company": "1074",
            "department": "1064",
            "title": "1065",
        }
        del all_facts[field_to_id[missing_field]]

        task_data = {
            "Id": run.task_id,
            "ServiceId": 53,
            "Name": "Создать пользователя",
            "Description": "Создать пользователя в Active Directory",
            "_field_meta": {"raw": all_facts},
        }

        orchestrator = TicketRunOrchestrator(db)
        result = await orchestrator.advance(
            run_id=run.id,
            task=task_data,
            event_type="ticket_created",
            actor="test",
        )

        assert result.run.current_step == "request_clarification"
        assert result.command is not None
        assert result.command.action == "apply_triage"
        assert result.command.params_json["status_id"] == 35  # Требует уточнения
        assert missing_field in str(result.envelope.outcome.missing_fields)
