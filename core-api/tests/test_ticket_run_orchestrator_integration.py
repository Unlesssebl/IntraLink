"""Integration tests for durable TicketRunOrchestrator lifecycle and state machine."""

from __future__ import annotations

import datetime as dt
import uuid
import pytest
from sqlalchemy import select

from shared.domain import (
    FactSource,
    FactState,
)
from app.database.db import (
    AsyncSessionLocal,
    CommandRecord,
    ResolutionPolicy,
    ResponseTemplate,
    TicketRun,
    TicketRunEvent,
)
from app.services.scenario_orchestrator import TicketRunOrchestrator, ticket_event_key
from app.services.ticket_runs import TicketRunState


async def _seed_templates_and_policies(db):
    templates = [
        ResponseTemplate(
            key="account_details_clarify",
            version=1,
            name="Уточнение реквизитов",
            template_text="Уточните, пожалуйста, реквизиты сотрудника.",
            required_variables=[],
            is_active=True,
            created_by="test",
        ),
        ResponseTemplate(
            key="create_user_proposed",
            version=1,
            name="Создание учётной записи",
            template_text="Учётная запись создаётся.",
            required_variables=[],
            is_active=True,
            created_by="test",
        ),
        ResponseTemplate(
            key="install_printer_proposed",
            version=1,
            name="Установка принтера",
            template_text="Принтер устанавливается.",
            required_variables=[],
            is_active=True,
            created_by="test",
        ),
        ResponseTemplate(
            key="resolved_standard",
            version=1,
            name="Заявка выполнена",
            template_text="Добрый день! Заявка успешно выполнена.",
            required_variables=[],
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

    # Map keys to template IDs
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
            outcome_key="install_printer_proposed",
            version=1,
            outcome_kind="action",
            template_id=tmpl_map["install_printer_proposed"],
            target_status_id=None,
            status_name=None,
            expenses=10,
            action_id="install_printer",
            risk_level=1,
            requires_approval=True,
            is_active=True,
            created_by="test",
        ),
        ResolutionPolicy(
            outcome_key="resolved_standard",
            version=1,
            outcome_kind="resolution",
            template_id=tmpl_map["resolved_standard"],
            target_status_id=29,
            status_name="Выполнена",
            expenses=15,
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


async def _create_test_run(db, task_id: int = 91001) -> TicketRun:
    run = TicketRun(
        id=uuid.uuid4(),
        task_id=task_id,
        mode="autopilot",
        state=TicketRunState.RUNNING.value,
        trigger_kind="ticket_created",
        trigger_key=f"ticket:{task_id}:created",
        trigger_snapshot_json={"scenario_key": "create_user"},
        scenario_key="create_user",
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


@pytest.mark.asyncio
async def test_orchestrator_clarification_and_resume():
    """Проверка полного цикла: нехватка данных -> уточнение -> комментарий заявителя -> продолжение."""
    async with AsyncSessionLocal() as db:
        await _seed_templates_and_policies(db)
        run = await _create_test_run(db, task_id=92001)

        task_incomplete = {
            "Id": 92001,
            "ServiceId": 53,
            "Name": "Создать учетную запись",
            "Description": "Новый сотрудник",
            "_field_meta": {"raw": {"1057": "test", "1058": "тест"}},
        }

        orchestrator = TicketRunOrchestrator(db)

        # 1. Первый advance: факты неполны, формируется запрос уточнения
        result1 = await orchestrator.advance(
            run_id=run.id,
            task=task_incomplete,
            event_type="ticket_created",
        )
        assert result1.run.current_step == "request_clarification"
        assert result1.command is not None
        assert result1.command.action == "apply_triage"
        assert result1.command.params_json["status_id"] == 35

        # 2. Имитация исполнения команды apply_triage воркером
        result1.command.status = "succeeded"
        await db.commit()

        # 3. Reconcile переводит заявку в waiting_answer
        result2 = await orchestrator.advance(
            run_id=run.id,
            task=task_incomplete,
            event_type="command_reconciled",
        )
        assert result2.run.state == TicketRunState.WAITING_ANSWER.value
        assert result2.run.waiting_reason == "clarification_requested"
        assert result2.run.clarification_count == 1

        # 4. Заявитель оставляет комментарий со всеми нужными данными
        comments = [
            {
                "Comment": "Иванов Иван Иванович, инженер, ИТ, Интра",
                "Author": "Заявитель",
                "Created": "2026-09-08T10:00:00Z",
            }
        ]
        task_updated = {
            **task_incomplete,
            "_field_meta": {
                "raw": {
                    "1057": "Иванов",
                    "1058": "Иван",
                    "1059": "Иванович",
                    "1065": "Инженер",
                    "1064": "ИТ",
                    "1074": "Интра",
                }
            },
        }

        # 5. Advance с новым комментарием возобновляет работу и формирует create_user command
        result3 = await orchestrator.advance(
            run_id=run.id,
            task=task_updated,
            comments=comments,
            event_type="comment_added",
        )
        assert result3.run.current_step == "execute:create_user"
        assert result3.command is not None
        assert result3.command.action == "create_user"
        assert result3.run.state == TicketRunState.WAITING_APPROVAL.value


@pytest.mark.asyncio
async def test_orchestrator_duplicate_event_idempotency():
    """Проверка дедупликации: повторный приход того же события не вызывает сайд-эффектов."""
    async with AsyncSessionLocal() as db:
        await _seed_templates_and_policies(db)
        run = await _create_test_run(db, task_id=92002)

        task = {
            "Id": 92002,
            "ServiceId": 53,
            "Name": "Создать учетную запись",
            "Description": "Новый сотрудник",
            "_field_meta": {"raw": {"1057": "test"}},
        }
        orchestrator = TicketRunOrchestrator(db)

        # Первый вызов
        res1 = await orchestrator.advance(run_id=run.id, task=task, event_type="ticket_created")
        assert res1.duplicate_event is False
        cmd_count_1 = (await db.scalars(select(CommandRecord).where(CommandRecord.ticket_run_id == run.id))).all()
        assert len(cmd_count_1) == 1

        # Повторный вызов с идентичным payload
        res2 = await orchestrator.advance(run_id=run.id, task=task, event_type="ticket_created")
        assert res2.duplicate_event is True
        cmd_count_2 = (await db.scalars(select(CommandRecord).where(CommandRecord.ticket_run_id == run.id))).all()
        assert len(cmd_count_2) == 1  # Никаких повторных команд не создано


@pytest.mark.asyncio
async def test_orchestrator_hitl_approval_lifecycle():
    """Проверка жизненного цикла HitL: waiting_approval -> одобрение -> running / отклонение -> paused."""
    async with AsyncSessionLocal() as db:
        await _seed_templates_and_policies(db)
        run = await _create_test_run(db, task_id=92003)

        task = {
            "Id": 92003,
            "ServiceId": 53,
            "Name": "Создание пользователя",
            "_field_meta": {
                "raw": {
                    "1057": "Сидоров",
                    "1058": "Сидор",
                    "1065": "Бухгалтер",
                    "1064": "Бухгалтерия",
                    "1074": "Интра",
                }
            },
        }
        orchestrator = TicketRunOrchestrator(db)

        # 1. Advance создает команду в awaiting_approval
        res = await orchestrator.advance(run_id=run.id, task=task, event_type="ticket_created")
        assert res.command.status == "awaiting_approval"
        assert res.run.state == TicketRunState.WAITING_APPROVAL.value

        # 2. Одобрение оператором через CommandService: переводит команду в queued и run в running
        from app.services.command_service import CommandService
        await CommandService(db).approve(
            command_id=res.command.id,
            decision="approve",
            reason="Одобрено инженером",
            operator="test_operator",
            approver_permissions=frozenset(["*"]),
        )
        await db.refresh(run)
        assert run.state == TicketRunState.RUNNING.value

        # 3. Имитация исполнения воркером
        res.command.status = "succeeded"
        res.command.result_json = {"created": True}
        res.command.version += 1
        await db.commit()

        # 4. Reconcile фиксирует завершение create_user и выполняет доставку
        from unittest.mock import patch
        with patch("app.services.command_delivery.CommandDeliveryService.deliver_create_user") as mock_deliver:
            mock_deliver.return_value = {"delivered": True}
            res_reconcile = await orchestrator.advance(
                run_id=run.id,
                task=task,
                event_type="command_reconciled",
                service_auth_b64="dGVzdDp0ZXN0",
            )
            assert res_reconcile.run.state == TicketRunState.COMPLETED.value
            assert res_reconcile.run.completed_at is not None

        # 5. Проверка отклонения (HitL rejection) на отдельном запуске
        run_reject = await _create_test_run(db, task_id=92099)
        task_reject = {**task, "Id": 92099}
        res2 = await orchestrator.advance(run_id=run_reject.id, task=task_reject, event_type="ticket_created")
        assert res2.command.status == "awaiting_approval"

        await CommandService(db).approve(
            command_id=res2.command.id,
            decision="reject",
            reason="Отклонено руководителем",
            operator="test_operator",
            approver_permissions=frozenset(["*"]),
        )
        await db.refresh(run_reject)
        assert run_reject.state == TicketRunState.PAUSED.value
        assert run_reject.pause_reason == "approval_rejected"


@pytest.mark.asyncio
async def test_orchestrator_lost_worker_and_error_handling():
    """Проверка сбоя воркера: failed / needs_review переводит заявку в paused с кодом ошибки."""
    async with AsyncSessionLocal() as db:
        await _seed_templates_and_policies(db)
        run = await _create_test_run(db, task_id=92004)

        task = {
            "Id": 92004,
            "ServiceId": 53,
            "Name": "Создание пользователя",
            "_field_meta": {
                "raw": {
                    "1057": "Федоров",
                    "1058": "Федор",
                    "1065": "Менеджер",
                    "1064": "Продажи",
                    "1074": "Интра",
                }
            },
        }
        orchestrator = TicketRunOrchestrator(db)
        res = await orchestrator.advance(run_id=run.id, task=task, event_type="ticket_created")

        # Воркер упал с ошибкой
        res.command.status = "failed"
        res.command.error_message = "Active Directory DC unavailable timeout"
        res.command.version += 1
        await db.commit()

        res_failed = await orchestrator.advance(
            run_id=run.id, task=task, event_type="command_reconciled"
        )
        assert res_failed.run.state == TicketRunState.PAUSED.value
        assert res_failed.run.pause_reason == "command_failed"
        assert res_failed.run.error_message == "Active Directory DC unavailable timeout"
        assert res_failed.run.completed_at is None


@pytest.mark.asyncio
async def test_orchestrator_restart_resilience():
    """Проверка устойчивости к перезапуску Core API: состояние загружается из БД без потерь."""
    async with AsyncSessionLocal() as db:
        await _seed_templates_and_policies(db)
        run = await _create_test_run(db, task_id=92005)

        run.state = TicketRunState.WAITING_ANSWER.value
        run.fact_revision = 3
        run.decision_version = 2
        run.scenario_key = "create_user"
        run.scenario_version = 1
        run.current_step = "request_clarification"
        await db.commit()
        run_id = run.id

    # Имитация нового экземпляра сервиса после перезапуска
    async with AsyncSessionLocal() as new_db:
        orchestrator = TicketRunOrchestrator(new_db)
        reloaded = await new_db.get(TicketRun, run_id)
        assert reloaded.fact_revision == 3
        assert reloaded.scenario_key == "create_user"
        assert reloaded.state == TicketRunState.WAITING_ANSWER.value


@pytest.mark.asyncio
async def test_orchestrator_reconcile_printer_finalization():
    """Проверка финализации принтера: успех воркера -> резолюция resolved_standard -> apply_triage 29 -> completed."""
    async with AsyncSessionLocal() as db:
        await _seed_templates_and_policies(db)
        run = TicketRun(
            id=uuid.uuid4(),
            task_id=92006,
            mode="autopilot",
            state=TicketRunState.RUNNING.value,
            trigger_kind="ticket_created",
            trigger_key="ticket:92006:created",
            trigger_snapshot_json={"scenario_key": "install_printer"},
            scenario_key="install_printer",
            scenario_version=1,
            fact_revision=1,
            decision_version=1,
            version=1,
            created_by="test",
            updated_by="test",
        )
        db.add(run)
        await db.commit()

        # Создаем команду установки принтера через CommandService
        from app.services.command_service import CommandService
        cmd, _ = await CommandService(db).create(
            action="install_printer",
            target={"pc_name": "PC-01"},
            parameters={"pc_name": "PC-01", "printer_name": "HP LaserJet"},
            idempotency_key=f"{run.id}:1:install_printer",
            initiator="test",
            source="test",
            priority=5,
            ticket_run_id=run.id,
        )
        cmd.status = "succeeded"
        cmd.version += 1
        await db.commit()

        task = {
            "Id": 92006,
            "ServiceId": 19,
            "Name": "Установить принтер",
            "Description": "HP LaserJet на PC-01",
        }
        orchestrator = TicketRunOrchestrator(db)

        # Reconcile должен разрешить resolved_standard и создать apply_triage команду со статусом 29
        res = await orchestrator.advance(
            run_id=run.id, task=task, event_type="command_reconciled"
        )
        assert res.command is not None
        assert res.command.action == "apply_triage"
        assert res.command.params_json["status_id"] == 29
        assert res.run.current_step == "finalize:apply_triage"

        # Имитация успешного выполнения apply_triage
        res.command.status = "succeeded"
        res.command.version += 1
        await db.commit()

        # Следующий reconcile завершает жизненный цикл TicketRun
        final_res = await orchestrator.advance(
            run_id=run.id, task=task, event_type="command_reconciled"
        )
        assert final_res.run.state == TicketRunState.COMPLETED.value
        assert final_res.run.completed_at is not None


@pytest.mark.asyncio
async def test_orchestrator_concurrent_version_conflict():
    """Проверка оптимистической блокировки версий: конфликт вызывает исключение."""
    from unittest.mock import patch

    async with AsyncSessionLocal() as db:
        await _seed_templates_and_policies(db)
        run = await _create_test_run(db, task_id=92007)
        run_id = run.id

        task = {
            "Id": 92007,
            "ServiceId": 53,
            "Name": "Создание пользователя",
        }
        orchestrator = TicketRunOrchestrator(db)

        # Симулируем параллельную транзакцию, инкрементирующую run.version во время сбора данных
        with patch("app.services.scenario_orchestrator.collect_ticket_observations") as mock_collect:
            async def concurrent_modify(*args, **kwargs):
                async with AsyncSessionLocal() as concurrent_db:
                    concurrent_run = await concurrent_db.get(TicketRun, run_id)
                    concurrent_run.version += 1
                    await concurrent_db.commit()
                return []

            mock_collect.side_effect = concurrent_modify

            with pytest.raises(ValueError, match="ticket_run_version_conflict"):
                await orchestrator.advance(run_id=run_id, task=task)
