"""Unit and integration tests for Taskiq broker and Command Dispatcher."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database.base import Base
from core.database.models import CommandRecord
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from worker.tests.test_autopilot_task import MockRedis
from worker.src.broker import (
    QUEUE_DEFAULT,
    QUEUE_RAG_COMPUTE,
    QUEUE_WINDOWS_EXEC,
    SUPPORTED_QUEUES,
    broker,
    get_broker_for_queue,
)
from worker.src.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult
from worker.src.scenarios.registry import ScenarioRegistry
from worker.src.services.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from worker.src.tasks.command_dispatcher import (
    dispatch_command_task,
    set_dispatcher_client,
    set_dispatcher_redis_client,
    set_dispatcher_registry,
    set_dispatcher_service_auth,
    set_session_factory,
)


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture(autouse=True)
def isolate_dispatcher_redis(mock_redis):
    set_dispatcher_redis_client(mock_redis)
    yield
    set_dispatcher_redis_client(None)


@pytest.fixture
def broker_queues():
    return SUPPORTED_QUEUES


def test_broker_configuration(broker_queues):
    assert broker.queue_name == QUEUE_DEFAULT
    assert QUEUE_DEFAULT in broker_queues
    assert QUEUE_RAG_COMPUTE in broker_queues
    assert QUEUE_WINDOWS_EXEC in broker_queues

    win_broker = get_broker_for_queue(QUEUE_WINDOWS_EXEC)
    assert win_broker.queue_name == QUEUE_WINDOWS_EXEC

    rag_broker = get_broker_for_queue(QUEUE_RAG_COMPUTE)
    assert rag_broker.queue_name == QUEUE_RAG_COMPUTE

    with pytest.raises(ValueError, match="Unsupported queue"):
        get_broker_for_queue("non_existent_queue")


@pytest.fixture
async def async_db():
    """Create in-memory SQLite database for isolated dispatcher tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        # Create tables (only CommandRecord is needed)
        await conn.run_sync(Base.metadata.create_all)

    # Set hook in command_dispatcher
    set_session_factory(session_factory)

    yield session_factory

    set_session_factory(None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_command_success(async_db):
    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-echo-001",
            action="test_action",
            executor="api",
            target_json={"ticket_id": 1001},
            params_json={"payload": "test-data"},
            status="pending",
            initiator="test-user",
            task_id=1001,
        )
        session.add(cmd)
        await session.commit()

    # Dispatch command
    res = await dispatch_command_task(cmd_id)

    assert res["status"] == "succeeded"
    assert res["command_id"] == str(cmd_id)

    # Verify state in database
    async with async_db() as session:
        stmt = select(CommandRecord).where(CommandRecord.id == cmd_id)
        saved = (await session.execute(stmt)).scalar_one()
        assert saved.status == "succeeded"
        assert saved.result_json is not None
        assert saved.result_json["status"] == "succeeded"
        assert saved.result_json["echo_params"]["payload"] == "test-data"
        assert saved.error_message is None


@pytest.mark.asyncio
async def test_dispatch_command_string_id(async_db):
    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-str-001",
            action="echo",
            executor="api",
            target_json={"ticket_id": 2002},
            params_json={"test": 123},
            status="pending",
            initiator="test-user",
        )
        session.add(cmd)
        await session.commit()

    # Pass command_id as string
    res = await dispatch_command_task(str(cmd_id))
    assert res["status"] == "succeeded"
    assert res["command_id"] == str(cmd_id)


@pytest.mark.asyncio
async def test_dispatch_command_install_printer(async_db):
    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-printer-001",
            action="install_printer",
            executor="worker",
            target_json={"ticket_id": 3003},
            params_json={"pc_name": "WKS-100", "printer_name": "HP LaserJet M402"},
            status="pending",
            initiator="autopilot",
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "succeeded"

    async with async_db() as session:
        stmt = select(CommandRecord).where(CommandRecord.id == cmd_id)
        saved = (await session.execute(stmt)).scalar_one()
        assert saved.status == "succeeded"
        assert saved.result_json["host"] == "WKS-100"
        assert saved.result_json["printer"] == "HP LaserJet M402"


@pytest.mark.asyncio
async def test_dispatch_command_missing_params_failure(async_db):
    cmd_id = uuid.uuid4()
    async with async_db() as session:
        # install_printer missing printer_name
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-fail-001",
            action="install_printer",
            executor="worker",
            target_json={"ticket_id": 4004},
            params_json={"pc_name": "WKS-100"},
            status="pending",
            initiator="autopilot",
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "failed"
    assert "printer_name" in res["error_message"]

    async with async_db() as session:
        stmt = select(CommandRecord).where(CommandRecord.id == cmd_id)
        saved = (await session.execute(stmt)).scalar_one()
        assert saved.status == "failed"
        assert "printer_name" in saved.error_message
        assert saved.result_json["failure_kind"] == "execution_error"


@pytest.mark.asyncio
async def test_dispatch_command_unknown_action(async_db):
    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-unknown-001",
            action="unknown_action_xyz",
            executor="worker",
            target_json={},
            params_json={},
            status="pending",
            initiator="user",
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "failed"
    assert "Unknown action" in res["error"]


@pytest.mark.asyncio
async def test_dispatch_command_idempotency_skips_completed(async_db):
    cmd_id = uuid.uuid4()
    existing_result = {"status": "succeeded", "note": "already done"}
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-idem-002",
            action="test_action",
            executor="api",
            target_json={},
            params_json={},
            status="succeeded",
            result_json=existing_result,
            initiator="user",
        )
        session.add(cmd)
        await session.commit()

    # Re-dispatching already succeeded command
    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "succeeded"
    assert res["result"] == existing_result


@pytest.mark.asyncio
async def test_dispatch_command_not_found(async_db):
    missing_id = uuid.uuid4()
    res = await dispatch_command_task(missing_id)
    assert res["status"] == "failed"
    assert "Command not found" in res["error"]


@pytest.mark.asyncio
async def test_dispatch_ad_actions(async_db):
    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-ad-001",
            action="ad_password_reset",
            executor="worker",
            target_json={},
            params_json={"sam_account_name": "ivanov.i"},
            status="pending",
            initiator="autopilot",
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "succeeded"
    assert res["result"]["account"] == "ivanov.i"


@pytest.mark.asyncio
async def test_dispatch_cancel_ticket(async_db):
    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-cancel-001",
            action="cancel_duplicate",
            executor="worker",
            target_json={"ticket_id": 5555},
            params_json={"comment": "Дубликат заявки #5550"},
            status="pending",
            initiator="autopilot",
            task_id=5555,
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "succeeded"
    assert res["result"]["status_applied"] == 30
    assert res["result"]["public_comment"] == "Дубликат заявки #5550"


class DummyScenario(BaseScenario):
    scenario_key = "dummy_scenario"
    name = "Тестовый сценарий"
    description = "Тестирование диспетчера"

    async def can_handle(self, task: TaskDTO) -> bool:
        return True

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: Any) -> ScenarioExecutionResult:
        return ScenarioExecutionResult(
            success=True,
            action_taken="dummy_action",
            resolution_comment="Регламентный ответ заявителю.",
            technical_note="Дополнительные детали аудита.",
            target_status_id=3,
        )


@pytest.fixture
def mock_dispatcher_intraservice():
    from unittest.mock import AsyncMock

    mock_client = AsyncMock()
    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="bW9jazp0b2tlbg==",
        bot_user_id=999,
        login="alen_assistant",
    )

    registry = ScenarioRegistry()
    registry.register(DummyScenario())

    set_dispatcher_client(mock_client)
    set_dispatcher_service_auth(mock_auth)
    set_dispatcher_registry(registry)

    yield mock_client, mock_auth, registry

    set_dispatcher_client(None)
    set_dispatcher_service_auth(None)
    set_dispatcher_registry(None)


@pytest.mark.asyncio
async def test_dispatch_canonical_scenario_execution_with_dual_audit(async_db, mock_dispatcher_intraservice):
    mock_client, _, _ = mock_dispatcher_intraservice
    mock_client.get_task.return_value = TaskDTO(
        id=7777,
        name="Настройка принтера",
        status_id=1,
        status_name="Новая",
        entities=ExtractedEntitiesDTO(pc_name="WKS-999"),
    )

    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-canonical-001",
            action="dummy_scenario",
            executor="worker",
            target_json={"ticket_id": 7777},
            params_json={"expected_status_id": 1, "override_comment": "Кастомный комментарий оператора"},
            status="pending",
            initiator="supervisor:petrov",
            task_id=7777,
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "succeeded"

    # Verify update_task was called for resolution comment and technical audit note
    assert mock_client.update_task.await_count == 2
    calls = mock_client.update_task.await_args_list

    # First call: resolution comment & status 3 transition
    call1 = calls[0].kwargs
    assert call1["task_id"] == 7777
    assert call1["status_id"] == 3
    assert call1["comment"] == "Кастомный комментарий оператора"
    assert call1["is_private"] is False

    # Second call: internal audit note with dual attribution
    call2 = calls[1].kwargs
    assert call2["task_id"] == 7777
    assert call2["is_private"] is True
    assert "Одобрил: supervisor:petrov" in call2["comment"]
    assert "Исполнил: alen_assistant" in call2["comment"]


@pytest.mark.asyncio
async def test_dispatch_occ_version_guard_conflict(async_db, mock_dispatcher_intraservice):
    mock_client, _, _ = mock_dispatcher_intraservice
    # Ticket status has changed from 1 to 2
    mock_client.get_task.return_value = TaskDTO(
        id=7778,
        name="Заявка",
        status_id=2,
        status_name="В работе",
        entities=ExtractedEntitiesDTO(),
    )

    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-occ-001",
            action="dummy_scenario",
            executor="worker",
            target_json={"ticket_id": 7778},
            params_json={"expected_status_id": 1},
            status="pending",
            initiator="supervisor:petrov",
            task_id=7778,
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "failed"
    assert "Статус заявки изменился с 1 на 2" in res["error_message"]


@pytest.mark.asyncio
async def test_dispatch_optimistic_lock_already_closed(async_db, mock_dispatcher_intraservice):
    mock_client, _, _ = mock_dispatcher_intraservice
    # Ticket already closed
    mock_client.get_task.return_value = TaskDTO(
        id=7779,
        name="Заявка закрыта",
        status_id=3,
        status_name="Выполнена",
        entities=ExtractedEntitiesDTO(),
    )

    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-closed-001",
            action="dummy_scenario",
            executor="worker",
            target_json={"ticket_id": 7779},
            params_json={},
            status="pending",
            initiator="supervisor:petrov",
            task_id=7779,
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "skipped"
    assert res["result"]["reason"] == "already_closed"


@pytest.mark.asyncio
async def test_dispatch_cooperative_cancellation_reclaimed(async_db, mock_dispatcher_intraservice, mock_redis):
    # Set operator abort flag in Redis
    await mock_redis.set("autopilot:abort:7780", "petrov")

    cmd_id = uuid.uuid4()
    async with async_db() as session:
        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key="test-reclaim-001",
            action="dummy_scenario",
            executor="worker",
            target_json={"ticket_id": 7780},
            params_json={},
            status="pending",
            initiator="supervisor:petrov",
            task_id=7780,
        )
        session.add(cmd)
        await session.commit()

    res = await dispatch_command_task(cmd_id)
    assert res["status"] == "aborted"
    assert "перехвачена оператором" in res["error_message"]
