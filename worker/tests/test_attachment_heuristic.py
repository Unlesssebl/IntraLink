"""Unit tests for Attachment Heuristic and Dialogue suspension in Autopilot task."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.autopilot.dto import AutopilotPolicyUpdateDTO
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
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

    async def delete(self, *keys: str):
        for k in keys:
            self.store.pop(k, None)

    async def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.store)


@pytest.fixture
async def test_session_factory():
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
    yield
    set_autopilot_redis_client(None)


@pytest.fixture
def policy_service(test_session_factory, mock_redis) -> AutopilotPolicyService:
    return AutopilotPolicyService(session_factory=test_session_factory, redis_client=mock_redis)


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock(spec=IntraServiceClient)
    client.update_task.return_value = True
    client.get_task_lifetime.return_value = []
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


@pytest.mark.asyncio
async def test_attachment_heuristic_escalates_to_human_when_facts_missing(
    mock_client,
    mock_service_auth,
    test_session_factory,
    policy_service,
):
    """Edge Case 6: If facts are missing but attachments are present, escalate to engineer instead of asking silly questions."""
    # Ensure install_printer policy is in FULL_AUTO mode
    await policy_service.update_policy("install_printer", AutopilotPolicyUpdateDTO(mode="FULL_AUTO", min_confidence=0.5))

    # Ticket missing PC name and IP address, but has attached scan
    task_with_scan = TaskDTO(
        Id=601,
        Name="Подключить сетевой принтер",
        Description="Прошу подключить сетевой принтер, все реквизиты во вложенном файле.",
        ServiceId=12,
        ServiceName="Оргтехника и печать",
        StatusId=1,
        StatusName="Новая",
        ExecutorIds="999",
        Entities=ExtractedEntitiesDTO(pc_name="", printer_address=""),
        Attachments=[{"Id": 1001, "Name": "printer_request_scan.pdf", "Size": 204800}],
    )
    mock_client.get_task.return_value = task_with_scan

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(601)

    assert res["status"] == "escalated_attachments_present"
    assert res["attachments_count"] == 1

    # Verify task was escalated to Status 2 (In Progress), NOT Status 6 (Suspended)
    calls = mock_client.update_task.call_args_list
    assert len(calls) == 1
    escalation_call = calls[0].kwargs
    assert escalation_call["status_id"] == 2
    assert "Требуется визуальный осмотр вложений" in escalation_call["comment"]
    assert escalation_call["is_private"] is True


@pytest.mark.asyncio
async def test_missing_facts_without_attachments_suspends_ticket(
    mock_client,
    mock_service_auth,
    test_session_factory,
    policy_service,
):
    """When facts are missing and NO attachments exist, ticket is suspended (Status 6) with clarification question."""
    await policy_service.update_policy("install_printer", AutopilotPolicyUpdateDTO(mode="FULL_AUTO", min_confidence=0.5))

    # Ticket missing PC name and IP address, NO attachments
    task_without_scan = TaskDTO(
        Id=602,
        Name="Подключить сетевой принтер",
        Description="Не печатает принтер, помогите",
        ServiceId=12,
        ServiceName="Оргтехника и печать",
        StatusId=1,
        StatusName="Новая",
        ExecutorIds="999",
        Entities=ExtractedEntitiesDTO(pc_name="", printer_address=""),
        Attachments=[],
    )
    mock_client.get_task.return_value = task_without_scan

    set_autopilot_client(mock_client)
    set_autopilot_service_auth(mock_service_auth)
    set_autopilot_session_factory(test_session_factory)
    set_autopilot_policy_service(policy_service)

    res = await autopilot_task(602)

    assert res["status"] == "paused_waiting_applicant"
    assert res["round"] == 1

    # Verify task was moved to Status 6 (Suspended) with a public clarification question
    calls = mock_client.update_task.call_args_list
    assert len(calls) == 2
    public_question = calls[0].kwargs
    assert public_question["status_id"] == 6
    assert public_question["is_private"] is False
    assert "Здравствуйте!" in public_question["comment"]
