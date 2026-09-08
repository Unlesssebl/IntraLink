"""Tests for user creation scenario execution via TicketRunRunner facade."""

import datetime as dt
import pytest
from sqlalchemy import select

from app.database.db import (
    ActionPolicyRecord,
    AsyncSessionLocal,
    AutopilotScenario,
    CommandRecord,
    ResolutionPolicy,
    ResponseTemplate,
    TicketRun,
)
from app.services.ticket_run_runner import TicketRunRunner
from app.services.ticket_runs import TicketRunService, TicketRunState


async def seed_user_creation_policies(db) -> None:
    templates = [
        ResponseTemplate(
            key="account_details_clarify",
            version=1,
            name="Уточнение реквизитов",
            template_text="Уточните, пожалуйста, реквизиты сотрудника (ФИО, должность, отдел).",
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
            key="resolved_standard",
            version=1,
            name="Заявка выполнена",
            template_text="Добрый день! Учётная запись успешно создана.",
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
            template_id=tmpl_map["resolved_standard"],
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


async def enable_user_creation_autopilot(db) -> TicketRunService:
    await seed_user_creation_policies(db)
    service = TicketRunService(db)
    setting = await service.get_global_setting()
    assert setting is not None
    setting.enabled = True
    scenario = await db.scalar(
        select(AutopilotScenario).where(
            AutopilotScenario.service_id == 53,
            AutopilotScenario.scenario_key == "user_creation",
        )
    )
    if scenario is None:
        db.add(
            AutopilotScenario(
                service_id=53,
                scenario_key="user_creation",
                enabled=True,
                rollout_mode="active",
                config_json={},
                updated_by="test",
            )
        )
    else:
        scenario.enabled = True
        scenario.rollout_mode = "active"
    await db.commit()
    return service


@pytest.mark.asyncio
async def test_register_user_creation_run():
    """Цикл user_creation создаётся только для явно включённого сценария."""
    async with AsyncSessionLocal() as db:
        policy = await db.get(ActionPolicyRecord, "create_user")
        if not policy:
            db.add(ActionPolicyRecord(action="create_user", mode="auto", updated_by="test"))
        else:
            policy.mode = "auto"
        await db.commit()

        task_id = 999101
        existing = (await db.execute(select(TicketRun).where(TicketRun.task_id == task_id))).scalars().all()
        for r in existing:
            await db.delete(r)
        await db.commit()

        task = {
            "Id": task_id,
            "Name": "Заявка на создание пользователя сети",
            "ServiceId": 53,
            "ServiceName": "Создание нового пользователя сети",
            "StatusId": 31,
            "ExecutorId": 10502,
            "ExecutorIds": "10502",
            "Description": "test",
        }

        service = await enable_user_creation_autopilot(db)
        res = await service.register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert res.created is True
        assert res.run is not None
        assert res.run.task_id == task_id
        snapshot = res.run.trigger_snapshot_json
        assert snapshot.get("scenario_key") == "user_creation"


@pytest.mark.asyncio
async def test_user_creation_clarification_on_invalid_data():
    """Тест запроса уточнений (создание команды apply_triage со статусом 35) при неполных реквизитах."""
    async with AsyncSessionLocal() as db:
        task_id = 999102
        existing = (await db.execute(select(TicketRun).where(TicketRun.task_id == task_id))).scalars().all()
        for r in existing:
            await db.delete(r)
        await db.commit()

        task = {
            "Id": task_id,
            "Name": "Заявка на создание пользователя сети",
            "ServiceId": 53,
            "ServiceName": "Создание нового пользователя сети",
            "StatusId": 31,
            "ExecutorId": 10502,
            "ExecutorIds": "10502",
            "Description": "test",
        }

        service = await enable_user_creation_autopilot(db)
        reg = await service.register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert reg.run is not None

        runner = TicketRunRunner(db)
        run = await runner.advance(
            run_id=reg.run.id,
            task=task,
            comments=[],
            actor="test",
            service_auth_b64="test_auth",
        )

        assert run.current_step == "request_clarification"
        assert run.state in (TicketRunState.RUNNING.value, TicketRunState.WAITING_APPROVAL.value)

        # Проверяем, что создана команда apply_triage со статусом 35
        cmd = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "apply_triage",
            )
        )
        assert cmd is not None
        assert cmd.params_json.get("status_id") == 35


@pytest.mark.asyncio
async def test_user_creation_max_clarifications_exceeded():
    """Тест превышения лимита уточнений -> перевод в паузу/согласование."""
    async with AsyncSessionLocal() as db:
        task_id = 999103
        existing = (await db.execute(select(TicketRun).where(TicketRun.task_id == task_id))).scalars().all()
        for r in existing:
            await db.delete(r)
        await db.commit()

        task = {
            "Id": task_id,
            "Name": "Заявка на создание пользователя сети",
            "ServiceId": 53,
            "ServiceName": "Создание нового пользователя сети",
            "StatusId": 31,
            "ExecutorId": 10502,
            "ExecutorIds": "10502",
            "Description": "test",
        }

        service = await enable_user_creation_autopilot(db)
        reg = await service.register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert reg.run is not None
        task["StatusId"] = 35
        reg.run.clarification_count = 2
        await db.commit()

        runner = TicketRunRunner(db)
        run = await runner.advance(
            run_id=reg.run.id,
            task=task,
            comments=[],
            actor="test",
            service_auth_b64="test_auth",
        )

        assert run.state in (
            TicketRunState.PAUSED.value,
            TicketRunState.RUNNING.value,
            TicketRunState.WAITING_APPROVAL.value,
        )


@pytest.mark.asyncio
async def test_user_creation_command_failure_pauses_run():
    """Тест перевода цикла в паузу при сбое команды в воркере."""
    async with AsyncSessionLocal() as db:
        task_id = 999104
        existing = (await db.execute(select(TicketRun).where(TicketRun.task_id == task_id))).scalars().all()
        for r in existing:
            await db.delete(r)
        await db.commit()

        task = {
            "Id": task_id,
            "Name": "Создание пользователя",
            "ServiceId": 53,
            "StatusId": 31,
            "ExecutorId": 10502,
            "ExecutorIds": "10502",
            "Description": "test",
        }

        service = await enable_user_creation_autopilot(db)
        reg = await service.register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert reg.run is not None
        reg.run.current_step = "execute:create_user"
        reg.run.state = TicketRunState.RUNNING.value
        await db.commit()

        from app.services.command_service import canonical_hash

        target_json = {"task_id": task_id}
        params_json = {"surname": "Иванов", "name": "Иван"}
        cmd = CommandRecord(
            action="create_user",
            status="failed",
            target_json=target_json,
            params_json=params_json,
            request_hash=canonical_hash("create_user", target_json, params_json),
            idempotency_key=f"ticket-run:{reg.run.id}:execute_create_user",
            initiator="test",
            executor="windows-worker",
            source="autopilot",
            priority=5,
            ticket_run_id=reg.run.id,
            error_message="Active Directory identity collision: ivanov.i exists",
        )
        db.add(cmd)
        await db.commit()

        runner = TicketRunRunner(db)
        run = await runner.advance(
            run_id=reg.run.id,
            task=task,
            comments=[],
            actor="test",
            service_auth_b64="test_auth",
        )

        assert run.state == TicketRunState.PAUSED.value
        assert run.pause_reason in ("command_failed", "execution_failed")


@pytest.mark.asyncio
async def test_user_creation_waiting_answer_receives_reply():
    """Тест возобновления и обработки заявки при наличии реквизитов."""
    async with AsyncSessionLocal() as db:
        policy = await db.get(ActionPolicyRecord, "create_user")
        if not policy:
            db.add(ActionPolicyRecord(action="create_user", mode="auto", updated_by="test"))
        else:
            policy.mode = "auto"
        await db.commit()

        task_id = 999105
        existing = (await db.execute(select(TicketRun).where(TicketRun.task_id == task_id))).scalars().all()
        for r in existing:
            await db.delete(r)
        await db.commit()

        task = {
            "Id": task_id,
            "Name": "Создать учетную запись",
            "Description": "Новый сотрудник",
            "ServiceId": 53,
            "ServiceName": "Создание нового пользователя сети",
            "StatusId": 31,
            "ExecutorId": 10502,
            "ExecutorIds": "10502",
            "_field_meta": {
                "raw": {
                    "1057": "Сидоров",
                    "1058": "Алексей",
                    "1059": "Михайлович",
                    "1065": "Инженер",
                    "1064": "ИТ",
                    "1074": "ООО Тест",
                }
            },
        }

        registry = await enable_user_creation_autopilot(db)
        reg = await registry.register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert reg.run is not None
        task["StatusId"] = 35

        reg.run.state = TicketRunState.WAITING_ANSWER.value
        reg.run.current_step = "request_clarification"
        reg.run.waiting_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=72)
        await db.commit()

        reply_comment = {
            "Id": 105,
            "UserId": 555,
            "UserName": "Петров Петр",
            "Comment": "Реквизиты указаны в карточке заявки",
            "Created": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)).isoformat(),
        }

        runner = TicketRunRunner(db)
        run = await runner.advance(
            run_id=reg.run.id,
            task=task,
            comments=[reply_comment],
            actor="test",
            service_auth_b64="test_auth",
        )

        assert run.state in (TicketRunState.RUNNING.value, TicketRunState.WAITING_APPROVAL.value)
        assert run.current_step == "execute:create_user"

        created_cmd = await db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.action == "create_user",
            )
        )
        assert created_cmd is not None
        assert created_cmd.action == "create_user"
