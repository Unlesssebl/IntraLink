"""Comprehensive integration tests for Autopilot task, Dialogue Loop, and Circuit Breaker."""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.autopilot.dto import AutopilotPolicyUpdateDTO
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO, TaskLifetimeEventDTO
from worker.src.tasks.autopilot import (
    autopilot_task,
    set_autopilot_client,
    set_autopilot_policy_service,
    set_autopilot_redis_client,
    set_autopilot_service_auth,
    set_autopilot_session_factory,
    set_full_auto_feature_gate,
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

    async def delete(self, *keys: str):
        deleted = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                deleted += 1
        return deleted

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.store)

    async def eval(self, script: str, numkeys: int, key: str, *args):
        del numkeys
        owner_token = args[0] if args else None
        if owner_token is not None and self.store.get(key) != owner_token:
            return 0
        if "expire" in script.lower():
            return 1
        return await self.delete(key)


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


@pytest.fixture(autouse=True)
def isolate_autopilot_redis(mock_redis):
    set_autopilot_redis_client(mock_redis)
    with patch(
        "core.routing.providers.semantic.get_embedding_vector",
        new_callable=AsyncMock,
        return_value=[0.0] * 1536,
    ), patch(
        "worker.src.tasks.command_dispatcher.dispatch_command_task.kiq",
        new_callable=AsyncMock,
    ), patch(
        "core.routing.verifier.transport.LiteLLMVerifierTransport.complete_json",
        new_callable=AsyncMock,
        return_value="{}",
    ):
        yield
    set_autopilot_redis_client(None)
    set_full_auto_feature_gate(None)


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

    # In ASSISTED mode, assigning the bot without an approved operator command
    # returns awaiting_operator_approval and does not create commands or mutate ticket.
    res = await autopilot_task(504)
    assert res["status"] == "awaiting_operator_approval"
    assert res["task_id"] == 504
    mock_client.update_task.assert_not_called()


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

    set_full_auto_feature_gate(True)
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
    assert "pc_name" in first_call["comment"]

    # Second call is private technical note
    second_call = mock_client.update_task.call_args_list[1].kwargs
    assert second_call["is_private"] is True
    assert "Запрос уточнения (раунд 1)" in second_call["comment"]


@pytest.mark.asyncio
async def test_autopilot_dialogue_without_prior_plan_remains_fail_closed(
    mock_client, mock_service_auth, test_session_factory, policy_service
):
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

    set_full_auto_feature_gate(True)
    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    with (
        patch("core.scenarios.adapters.install_printer.probe_diagnostic_ports", return_value=[{"port": 5985, "is_open": True}, {"port": 445, "is_open": True}]),
        patch("core.scenarios.adapters.install_printer.fast_ping", return_value={"host": "WKS-7777", "is_online": True}),
    ):
        res = await autopilot_task(506)

        assert res["status"] == "paused_waiting_applicant"

        # Without a previously policy-validated FULL_AUTO plan, applicant text
        # is not allowed to feed an autonomous mutation path.
        assert task.entities.pc_name == ""
        assert mock_client.update_task.call_count == 2
        res_call = mock_client.update_task.call_args_list[0].kwargs
        assert res_call["status_id"] == 6
        assert res_call["is_private"] is False


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

    set_full_auto_feature_gate(True)
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
# 4. Single execution owner handoff
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_auto_creates_command_without_executing_scenario_inline(
    mock_client, mock_service_auth, test_session_factory, policy_service, mock_redis
):
    import uuid
    from datetime import UTC, datetime, timedelta

    from core.database.models import PreparedPlanRecord, RoutingDecisionRecord, RoutingPreflightRecord
    from core.routing.preflight import compute_canonical_params_hash, compute_canonical_plan_hash_from_json
    from core.routing.snapshot import TicketSnapshotFactory
    task = TaskDTO(
        Id=508,
        Name="Подключение к корпоративной Wi-Fi сети",
        Description="Добавьте мою учётную запись в группу WLAN-WORKNET",
        StatusId=1,
        ExecutorIds="999",
        ApplicantName="Петров П.П.",
        ServiceId=63,
        entities=ExtractedEntitiesDTO(target_user="petrov.p"),
    )
    mock_client.get_task.return_value = task
    mock_client.get_task_lifetime.return_value = []

    await policy_service.update_policy("grant_wlan", AutopilotPolicyUpdateDTO(mode="FULL_AUTO"))

    snapshot = TicketSnapshotFactory.create(task, comments=[])
    decision_id = uuid.uuid4()
    preflight_id = uuid.uuid4()
    plan_data = {
        "task_id": task.id,
        "decision_id": str(decision_id),
        "snapshot_hash": snapshot.snapshot_hash,
        "scenario_key": "grant_wlan",
        "proposed_params": {"target_user": "petrov.p"},
        "suggested_comment": "",
        "target_status_id": 3,
        "routing_state": "selected",
        "is_executable": True,
    }
    plan_hash = compute_canonical_plan_hash_from_json(plan_data)
    async with test_session_factory() as session:
        session.add_all(
            [
                RoutingDecisionRecord(
                    id=decision_id,
                    task_id=task.id,
                    snapshot_hash=snapshot.snapshot_hash,
                    router_version="test",
                    state="selected",
                    selected_scenario="grant_wlan",
                ),
                RoutingPreflightRecord(
                    id=preflight_id,
                    decision_id=decision_id,
                    task_id=task.id,
                    snapshot_hash=snapshot.snapshot_hash,
                    scenario_key="grant_wlan",
                    params_hash=compute_canonical_params_hash(plan_data["proposed_params"]),
                    status="passed",
                    expires_at=datetime.now(UTC) + timedelta(minutes=2),
                ),
                PreparedPlanRecord(
                    task_id=task.id,
                    decision_id=decision_id,
                    snapshot_hash=snapshot.snapshot_hash,
                    plan_hash=plan_hash,
                    scenario_key="grant_wlan",
                    preflight_id=preflight_id,
                    plan_json=plan_data,
                    state="selected",
                ),
            ]
        )
        await session.commit()

    set_full_auto_feature_gate(True)
    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    with patch(
        "core.scenarios.adapters.grant_wlan.GrantWLANScenario.execute",
        new_callable=AsyncMock,
    ) as execute_mock:
        result = await autopilot_task(508)

    assert result["status"] == "command_dispatched"
    execute_mock.assert_not_awaited()
    assert "lock:task:508" not in mock_redis.store


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
    assert res["status"] == "awaiting_operator_approval"
    mock_client.update_task.assert_not_awaited()


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
    assert res["status"] == "awaiting_operator_approval"
    mock_client.update_task.assert_not_awaited()


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
    assert res["status"] == "awaiting_operator_approval"
    mock_client.update_task.assert_not_awaited()


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
    assert res["status"] == "awaiting_operator_approval"
    mock_client.update_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_autopilot_distributed_concurrency_lock(mock_client, mock_service_auth, test_session_factory, policy_service, mock_redis):
    # ASSISTED mode is read-only and does not depend on the execution lease.
    await mock_redis.set("lock:task:513", "locked", ex=60)
    mock_client.get_task.return_value = TaskDTO(
        Id=513,
        Name="Заявка",
        StatusId=1,
        ExecutorIds="999",
    )

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)
    set_autopilot_redis_client(mock_redis)

    res = await autopilot_task(513)
    assert res["status"] == "awaiting_operator_approval"
    mock_client.update_task.assert_not_awaited()
    assert mock_redis.store["lock:task:513"] == "locked"
    set_autopilot_redis_client(None)
