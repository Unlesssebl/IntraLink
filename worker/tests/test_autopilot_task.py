"""Comprehensive integration tests for Autopilot task, Dialogue Loop, and Circuit Breaker."""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.autopilot.dto import AutopilotPolicyUpdateDTO
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base
from core.database.models import CommandRecord
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO, TaskLifetimeEventDTO
from worker.src.services.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from worker.src.tasks.autopilot import (
    autopilot_task,
    set_autopilot_client,
    set_autopilot_policy_service,
    set_autopilot_redis_client,
    set_autopilot_service_auth,
    set_autopilot_session_factory,
)


class MockRedis:
    def __init__(self) -> None:
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key: str):
        self.store.pop(key, None)


@pytest.fixture
async def test_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture
def policy_service(test_session_factory, mock_redis) -> AutopilotPolicyService:
    return AutopilotPolicyService(session_factory=test_session_factory, redis_client=mock_redis)


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock(spec=IntraServiceClient)
    client.update_task.return_value = True
    return client


@pytest.fixture
def mock_service_auth() -> AsyncMock:
    auth_service = AsyncMock(spec=ServiceAuthBootstrap)
    auth_service.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="Ym90OnBhc3M=",
        bot_user_id=999,
        login="service_bot",
    )
    return auth_service


# -------------------------------------------------------------
# 1. Anti-Loop and Optimistic Lock Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_autopilot_skips_auto_reply(mock_client, mock_service_auth, test_session_factory, policy_service):
    auto_reply_task = TaskDTO(
        Id=501,
        Name="Автоответ: В отпуске до понедельника",
        Description="Я нахожусь в отпуске. Auto-generated email.",
        StatusId=1,
        ExecutorIds="999",
    )
    mock_client.get_task.return_value = auto_reply_task

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(501)
    assert res["status"] == "skipped"
    assert res["reason"] == "auto_reply_detected"
    mock_client.update_task.assert_not_called()


@pytest.mark.asyncio
async def test_autopilot_optimistic_lock_already_closed(mock_client, mock_service_auth, test_session_factory, policy_service):
    closed_task = TaskDTO(
        Id=502,
        Name="Настройка принтера",
        Description="Установить принтер",
        StatusId=3,  # Completed
        StatusName="Выполнена",
        ExecutorIds="999",
    )
    mock_client.get_task.return_value = closed_task

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(502)
    assert res["status"] == "skipped"
    assert res["reason"] == "already_closed"
    mock_client.update_task.assert_not_called()


@pytest.mark.asyncio
async def test_autopilot_optimistic_lock_reassigned_to_human(mock_client, mock_service_auth, test_session_factory, policy_service):
    human_task = TaskDTO(
        Id=503,
        Name="Настройка принтера",
        Description="Установить принтер",
        StatusId=2,  # In Progress
        ExecutorIds="42",  # Assigned to engineer 42, bot 999 is NOT assigned
    )
    mock_client.get_task.return_value = human_task

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(503)
    assert res["status"] == "skipped"
    assert res["reason"] == "assigned_to_human"
    mock_client.update_task.assert_not_called()


# -------------------------------------------------------------
# 2. ASSISTED Mode (Co-pilot ActionDock)
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_autopilot_assisted_mode(mock_client, mock_service_auth, test_session_factory, policy_service):
    task = TaskDTO(
        Id=504,
        Name="Установка принтера HP",
        Description="Подключите принтер",
        StatusId=1,
        ExecutorIds="999",
        entities=ExtractedEntitiesDTO(pc_name="WKS-1234", printer_address="10.1.2.3"),
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = []

    # Configure install_printer policy to ASSISTED
    await policy_service.update_policy("install_printer", AutopilotPolicyUpdateDTO(mode="ASSISTED"))

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(504)
    assert res["status"] == "assisted_prepared"
    assert res["scenario"] == "install_printer"

    # Verify CommandRecord is created in DB with status 'pending'
    async with test_session_factory() as session:
        stmt = select(CommandRecord).where(CommandRecord.task_id == 504)
        result = await session.execute(stmt)
        cmd = result.scalar_one_or_none()
        assert cmd is not None
        assert cmd.status == "pending"
        assert cmd.action == "install_printer"

    # Verify hidden note posted for engineers
    mock_client.update_task.assert_called_once()
    call_kwargs = mock_client.update_task.call_args.kwargs
    assert call_kwargs["is_private"] is True
    assert "Ко-пилота (ASSISTED)" in call_kwargs["comment"]


# -------------------------------------------------------------
# 3. Autonomous Dialogue Loop (Suspend -> Resume -> Limit)
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_autopilot_dialogue_missing_facts_suspends_ticket(mock_client, mock_service_auth, test_session_factory, policy_service):
    # Missing PC name
    task = TaskDTO(
        Id=505,
        Name="Установка принтера",
        Description="Подключите принтер на 10.1.1.20",
        StatusId=1,
        ExecutorIds="999",
        entities=ExtractedEntitiesDTO(printer_address="10.1.1.20"),
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = []

    await policy_service.update_policy("install_printer", AutopilotPolicyUpdateDTO(mode="FULL_AUTO"))

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(505)
    assert res["status"] == "paused_waiting_applicant"
    assert res["round"] == 1
    assert "pc_name" in res["missing_facts"]

    # Verify ticket suspended to Status 6 with public instruction
    assert mock_client.update_task.call_count == 2
    first_call = mock_client.update_task.call_args_list[0].kwargs
    assert first_call["status_id"] == 6
    assert first_call["is_private"] is False
    assert "укажите, пожалуйста, сетевое имя" in first_call["comment"]

    # Second call is private technical note
    second_call = mock_client.update_task.call_args_list[1].kwargs
    assert second_call["is_private"] is True
    assert "Запрос уточнения (раунд 1)" in second_call["comment"]


@pytest.mark.asyncio
async def test_autopilot_dialogue_resume_loop_enriches_and_resolves(mock_client, mock_service_auth, test_session_factory, policy_service):
    # Initial ticket state was missing PC name
    task = TaskDTO(
        Id=506,
        Name="Установка принтера",
        Description="Подключите принтер",
        StatusId=6,  # Paused
        ExecutorIds="999",
        entities=ExtractedEntitiesDTO(printer_address="10.1.1.20"),
    )
    mock_client.get_task.return_value = task

    # Applicant replied in lifetime history
    mock_client.get_task_lifetime.return_value = [
        TaskLifetimeEventDTO(StatusId=6, Comment="Здравствуйте! Укажите имя ПК", EditorId=999, IsPrivateComment=False),
        TaskLifetimeEventDTO(Comment="Мой системный блок WKS-7777, включен", EditorId=123, IsPrivateComment=False),
    ]

    await policy_service.update_policy("install_printer", AutopilotPolicyUpdateDTO(mode="FULL_AUTO"))

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    with (
        patch("worker.src.scenarios.install_printer.probe_diagnostic_ports", return_value={"smb_445": True, "winrm_5985": True}),
        patch("worker.src.tasks.printers.install_printer_task", new_callable=AsyncMock) as mock_prn,
    ):
        mock_prn.return_value = {"status": "ok"}
        res = await autopilot_task(506)

        assert res["status"] == "resolved"
        assert res["target_status_id"] == 3

        # Verify task completed (Status Transit Safeguard: 6 -> 2 -> 3)
        assert mock_client.update_task.call_count == 3
        transit_call = mock_client.update_task.call_args_list[0].kwargs
        assert transit_call["status_id"] == 2  # Intermediate transition from paused
        res_call = mock_client.update_task.call_args_list[1].kwargs
        assert res_call["status_id"] == 3  # Final completion
        assert "успешно настроен" in res_call["comment"]


@pytest.mark.asyncio
async def test_autopilot_dialogue_limit_escalates_to_human(mock_client, mock_service_auth, test_session_factory, policy_service):
    # Ticket has already undergone 2 clarification rounds without valid data
    task = TaskDTO(
        Id=507,
        Name="Установка принтера",
        Description="Установите принтер",
        StatusId=6,
        ExecutorIds="999",
        entities=ExtractedEntitiesDTO(),  # Still empty facts!
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = [
        TaskLifetimeEventDTO(StatusId=6, Comment="Запрос 1", EditorId=999),
        TaskLifetimeEventDTO(Comment="Что это такое?", EditorId=123),
        TaskLifetimeEventDTO(StatusId=6, Comment="Запрос 2", EditorId=999),
        TaskLifetimeEventDTO(Comment="Все равно не понимаю", EditorId=123),
    ]

    await policy_service.update_policy("install_printer", AutopilotPolicyUpdateDTO(mode="FULL_AUTO"))

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(507)
    assert res["status"] == "escalated_dialogue_limit"
    assert res["rounds"] >= 2

    # Verify escalated to human engineer (Status 2)
    mock_client.update_task.assert_called_once()
    escalate_call = mock_client.update_task.call_args.kwargs
    assert escalate_call["status_id"] == 2
    assert "Превышен лимит диалога" in escalate_call["comment"]


# -------------------------------------------------------------
# 4. Circuit Breaker Tripwire on Execution Failures
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_autopilot_circuit_breaker_trips_on_failures(mock_client, mock_service_auth, test_session_factory, policy_service):
    task = TaskDTO(
        Id=508,
        Name="Сброс пароля AD",
        Description="Сбросить пароль",
        StatusId=1,
        ExecutorIds="999",
        ApplicantName="Петров П.П.",
        entities=ExtractedEntitiesDTO(target_user="petrov.p"),
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = []

    # Configure ad_password_reset to FULL_AUTO
    await policy_service.update_policy("ad_password_reset", AutopilotPolicyUpdateDTO(mode="FULL_AUTO"))

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    with patch("worker.src.tasks.ad_actions.reset_ad_password_task", side_effect=Exception("LDAP Server Down")):
        # Failure 1
        res1 = await autopilot_task(508)
        assert res1["status"] == "failed"
        assert res1["circuit_broken"] is False

        # Failure 2
        res2 = await autopilot_task(508)
        assert res2["status"] == "failed"
        assert res2["circuit_broken"] is False

        # Failure 3 -> Trips Circuit Breaker!
        res3 = await autopilot_task(508)
        assert res3["status"] == "failed"
        assert res3["circuit_broken"] is True

        # Verify policy degraded to ASSISTED
        policy = await policy_service.get_policy("ad_password_reset")
        assert policy.mode == "ASSISTED"
        assert policy.is_circuit_broken is True


# -------------------------------------------------------------
# 5. Robustness & Edge-Cases Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_autopilot_edge_case_cancel_request(mock_client, mock_service_auth, test_session_factory, policy_service):
    # Applicant replies: "Уже не надо, решили сами"
    task = TaskDTO(
        Id=509,
        Name="Установка принтера",
        StatusId=6,
        ExecutorIds="999",
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = [
        TaskLifetimeEventDTO(StatusId=6, Comment="Укажите WKS", EditorId=999),
        TaskLifetimeEventDTO(Comment="Спасибо, уже не надо, все заработало!", EditorId=123),
    ]

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(509)
    assert res["status"] == "canceled_by_applicant"

    # Ticket automatically canceled with Status 30
    assert mock_client.update_task.call_count >= 1
    call_args = mock_client.update_task.call_args_list[0].kwargs
    assert call_args["status_id"] == 30
    assert "отменена по вашей просьбе" in call_args["comment"]


@pytest.mark.asyncio
async def test_autopilot_edge_case_clarification_question(mock_client, mock_service_auth, test_session_factory, policy_service):
    # Applicant replies: "А где посмотреть IP адрес принтера?"
    task = TaskDTO(
        Id=510,
        Name="Установка принтера",
        StatusId=6,
        ExecutorIds="999",
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = [
        TaskLifetimeEventDTO(StatusId=6, Comment="Укажите IP", EditorId=999),
        TaskLifetimeEventDTO(Comment="Подскажите, где посмотреть этот IP?", EditorId=123),
    ]

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(510)
    assert res["status"] == "helpful_hint_sent"

    # Helpful guide posted, status remains intact
    call_args = mock_client.update_task.call_args_list[0].kwargs
    assert "информационной наклейке" in call_args["comment"]


@pytest.mark.asyncio
async def test_autopilot_edge_case_home_subnet_rejection(mock_client, mock_service_auth, test_session_factory, policy_service):
    # Applicant gives home router IP: 192.168.1.120
    task = TaskDTO(
        Id=511,
        Name="Установка принтера",
        StatusId=6,
        ExecutorIds="999",
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = [
        TaskLifetimeEventDTO(StatusId=6, Comment="Укажите IP", EditorId=999),
        TaskLifetimeEventDTO(Comment="Мой IP принтера 192.168.1.120", EditorId=123),
    ]

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(511)
    assert res["status"] == "home_subnet_rejected"
    assert res["ip"] == "192.168.1.120"

    call_args = mock_client.update_task.call_args_list[0].kwargs
    assert "относится к домашней/локальной сети" in call_args["comment"]
    assert "10.***.***.***" in call_args["comment"]


@pytest.mark.asyncio
async def test_autopilot_edge_case_attachments_only(mock_client, mock_service_auth, test_session_factory, policy_service):
    from core.intraservice.dto import AttachmentDTO

    task = TaskDTO(
        Id=512,
        Name="Установка принтера",
        StatusId=6,
        ExecutorIds="999",
        Attachments=[AttachmentDTO(Id=99, Name="sticker_photo.jpg", Size=102400)],
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = [
        TaskLifetimeEventDTO(StatusId=6, Comment="Укажите IP", EditorId=999),
        TaskLifetimeEventDTO(Comment="Фото", EditorId=123),
    ]

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(512)
    assert res["status"] == "escalated_attachments_only"

    # Escalated to engineer (Status 2)
    call_args = mock_client.update_task.call_args_list[0].kwargs
    assert call_args["status_id"] == 2
    assert "Вложение от заявителя" in call_args["comment"]


@pytest.mark.asyncio
async def test_autopilot_distributed_concurrency_lock(mock_client, mock_service_auth, test_session_factory, policy_service, mock_redis):
    # Lock is already held by another worker
    await mock_redis.set("lock:autopilot:513", "locked", ex=60)

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    res = await autopilot_task(513)
    assert res["status"] == "skipped"
    assert res["reason"] == "concurrent_lock_active"
    mock_client.get_task.assert_not_called()
    set_autopilot_redis_client(None)
