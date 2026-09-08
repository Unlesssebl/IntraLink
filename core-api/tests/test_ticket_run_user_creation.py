import pytest
from unittest.mock import AsyncMock, patch
import uuid
import datetime as dt
from sqlalchemy import select

from app.database.db import (
    AsyncSessionLocal,
    ActionPolicyRecord,
    CommandRecord,
    TicketRun,
    TicketRunEvent,
)
from app.services.ticket_runs import TicketRunService, TicketRunState
from app.services.ticket_run_runner import TicketRunRunner
from app.services.actions.registry import PolicyMode


@pytest.mark.asyncio
async def test_register_user_creation_run():
    """Тест автоматической регистрации цикла user_creation без ручной настройки таблицы сценариев."""
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

        res = await TicketRunService(db).register_assignment(
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
    """Тест запроса уточнений (статус 35, адаптивный ответ) при неполных реквизитах (как 'test')."""
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

        reg = await TicketRunService(db).register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert reg.run is not None

        with patch("app.services.intraservice.update_task_full", new_callable=AsyncMock) as mock_update:
            mock_update.return_value = True
            runner = TicketRunRunner(db)
            run = await runner.advance(
                run_id=reg.run.id,
                task=task,
                comments=[],
                actor="test",
                service_auth_b64="test_auth",
            )

        assert run.state == TicketRunState.WAITING_ANSWER.value
        assert run.waiting_reason == "missing_person_details"
        assert run.clarification_count == 1

        mock_update.assert_called_once()
        _, kwargs = mock_update.call_args
        assert kwargs["task_id"] == task_id
        assert kwargs["status_id"] == 35
        assert kwargs["is_private"] is False
        assert "укажите" in kwargs["comment"].lower()


@pytest.mark.asyncio
async def test_user_creation_max_clarifications_exceeded():
    """Тест превышения лимита уточнений (2 попытки) -> скрытый комментарий и пауза."""
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
            "StatusId": 35,
            "ExecutorId": 10502,
            "ExecutorIds": "10502",
            "Description": "test",
        }

        reg = await TicketRunService(db).register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert reg.run is not None
        reg.run.clarification_count = 2
        await db.commit()

        with patch("app.services.intraservice.add_task_comment", new_callable=AsyncMock) as mock_comment:
            mock_comment.return_value = True
            runner = TicketRunRunner(db)
            run = await runner.advance(
                run_id=reg.run.id,
                task=task,
                comments=[],
                actor="test",
                service_auth_b64="test_auth",
            )

        assert run.state == TicketRunState.PAUSED.value
        assert run.pause_reason == "max_clarifications_exceeded"

        mock_comment.assert_called_once()
        args, kwargs = mock_comment.call_args
        called_task_id = kwargs.get("task_id", args[1] if len(args) > 1 else None)
        called_comment = kwargs.get("comment", args[2] if len(args) > 2 else None)
        called_is_private = kwargs.get("is_private", args[3] if len(args) > 3 else False)
        assert called_task_id == task_id
        assert called_is_private is True
        assert "[IntraLink AutoOps | System Diagnostic]" in called_comment


@pytest.mark.asyncio
async def test_user_creation_command_failure_hidden_comment():
    """Тест публикации скрытого комментария инженерам при сбое команды в AD."""
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

        reg = await TicketRunService(db).register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert reg.run is not None
        reg.run.current_step = "execute_create_user"
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

        with patch("app.services.intraservice.add_task_comment", new_callable=AsyncMock) as mock_comment:
            mock_comment.return_value = True
            runner = TicketRunRunner(db)
            run = await runner.advance(
                run_id=reg.run.id,
                task=task,
                comments=[],
                actor="test",
                service_auth_b64="test_auth",
            )

        assert run.state == TicketRunState.PAUSED.value
        assert run.pause_reason == "execution_failed"

        mock_comment.assert_called_once()
        args, kwargs = mock_comment.call_args
        called_task_id = kwargs.get("task_id", args[1] if len(args) > 1 else None)
        called_comment = kwargs.get("comment", args[2] if len(args) > 2 else None)
        called_is_private = kwargs.get("is_private", args[3] if len(args) > 3 else False)
        assert called_task_id == task_id
        assert called_is_private is True
        assert "Active Directory identity collision" in called_comment


@pytest.mark.asyncio
async def test_user_creation_waiting_answer_receives_reply():
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
            "Description": "test",
            "ServiceId": 53,
            "ServiceName": "Создание нового пользователя сети",
            "StatusId": 35,
            "ExecutorId": 10502,
            "ExecutorIds": "10502",
        }

        registry = TicketRunService(db)
        reg = await registry.register_assignment(
            task=task,
            assistant_user_id=10502,
            open_status_id=31,
            actor="test",
        )
        assert reg.run is not None

        # Имитируем, что раннер уже задал вопрос и перешел в WAITING_ANSWER
        reg.run.state = TicketRunState.WAITING_ANSWER.value
        reg.run.current_step = "wait_for_clarification"
        reg.run.waiting_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=72)
        reg.run.context_json = {"last_seen_comment_id": 100, "clarification_count": 1}
        await db.commit()

        # Появился новый комментарий от заявителя (UserId != 10502)
        reply_comment = {
            "Id": 105,
            "UserId": 555,
            "UserName": "Петров Петр",
            "Comment": "ФИО: Сидоров Алексей Михайлович\nДолжность: Инженер\nОтдел: ИТ\nОрганизация: ООО Тест",
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

        assert (run.error_code, run.error_message) == (None, None)
        assert run.state == TicketRunState.RUNNING.value
        assert run.current_step == "execute_create_user"
        assert run.waiting_reason is None

        # Проверяем, что команда create_user создана в БД со статусом queued
        created_cmd = await runner._command(run, "execute_create_user")
        assert created_cmd is not None
        assert created_cmd.action == "create_user"
        assert created_cmd.status == "queued"
        assert created_cmd.params_json.get("surname") == "Сидоров"
        assert created_cmd.params_json.get("name") == "Алексей"
        assert created_cmd.params_json.get("patronymic") == "Михайлович"

