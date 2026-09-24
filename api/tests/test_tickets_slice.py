import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.src.core.db import get_db_session
from api.src.main import app
from core.database.models import CommandRecord
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO


@pytest.fixture
def mock_task():
    return TaskDTO(
        Id=1001,
        Name="Проблема с принтером HP",
        Description="Бумага застряла",
        ServiceId=233,
        ServiceName="Установка Directum",
        StatusId=2,
        StatusName="В работе",
        PriorityName="Обычный",
        Created="2026-09-23 10:00",
        ApplicantName="Иванов И.И.",
        entities=ExtractedEntitiesDTO(pc_name="PC-TEST-01"),
    )


@pytest.mark.asyncio
async def test_list_tickets_endpoint(mock_task):
    transport = ASGITransport(app=app)
    with patch(
        "core.intraservice.client.IntraServiceClient.get_tasks_by_filter", new_callable=AsyncMock
    ) as mock_filter:
        mock_filter.return_value = [mock_task]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/tickets?filter_id=984")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["id"] == 1001
            assert data[0]["pc_name"] == "PC-TEST-01"


@pytest.mark.asyncio
async def test_get_single_ticket_endpoint(mock_task):
    transport = ASGITransport(app=app)
    with patch("core.intraservice.client.IntraServiceClient.get_task", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_task
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/tickets/1001")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == 1001
            assert data["status_name"] == "В работе"


@pytest.mark.asyncio
async def test_get_ticket_lifetime_endpoint():
    transport = ASGITransport(app=app)
    from core.intraservice.dto import TaskLifetimeEventDTO

    mock_events = [
        TaskLifetimeEventDTO(
            Id=1,
            TaskId=1001,
            Created="2026-09-23T16:55:00",
            UserName="Администратор",
            Comment="Тестовый комментарий",
            NewStatusName="В работе",
        )
    ]
    with patch("core.intraservice.client.IntraServiceClient.get_task_lifetime", new_callable=AsyncMock) as mock_lt:
        mock_lt.return_value = mock_events
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v2/tickets/1001/lifetime")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["comment"] == "Тестовый комментарий"
            assert data[0]["user_name"] == "Администратор"


@pytest.mark.asyncio
async def test_execute_ticket_action_enqueues_taskiq():
    fake_id = uuid.uuid4()
    mock_cmd = CommandRecord(
        id=fake_id,
        idempotency_key="test-key-001",
        action="install_printer",
        executor="api",
        target_json={"ticket_id": 1001},
        params_json={"printer_name": "HP LaserJet"},
        status="pending",
        initiator="web-user",
        task_id=1001,
    )
    mock_cmd.created_at = datetime.now(timezone.utc)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    async def override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    with patch("worker.src.tasks.command_dispatcher.dispatch_command_task.kiq", new_callable=AsyncMock) as mock_kiq:
        mock_kiq.return_value = None
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v2/tickets/1001/actions",
                json={
                    "action": "install_printer",
                    "params": {"printer_name": "HP LaserJet"},
                    "idempotency_key": "test-key-001",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["action"] == "install_printer"
            assert data["status"] == "pending"
            assert data["idempotency_key"] == "test-key-001"
            assert mock_kiq.await_count == 1

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_task_status_endpoint():
    cmd_id = uuid.uuid4()
    mock_cmd = CommandRecord(
        id=cmd_id,
        idempotency_key="cmd-key-777",
        action="install_printer",
        executor="worker",
        target_json={"ticket_id": 1001},
        params_json={"printer_name": "HP LaserJet"},
        status="succeeded",
        initiator="web-user",
        task_id=1001,
        result_json={"host": "WKS-01", "printer": "HP LaserJet"},
    )
    mock_cmd.created_at = datetime.now(timezone.utc)
    mock_cmd.updated_at = datetime.now(timezone.utc)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_cmd)))

    async def override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Check standard endpoint /api/v2/tasks/{command_id}
        resp = await client.get(f"/api/v2/tasks/{cmd_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["command_id"] == str(cmd_id)
        assert data["status"] == "succeeded"
        assert data["action"] == "install_printer"
        assert data["result_json"]["host"] == "WKS-01"

        # Check tickets alias endpoint /api/v2/tickets/tasks/{command_id}
        resp_alias = await client.get(f"/api/v2/tickets/tasks/{cmd_id}")
        assert resp_alias.status_code == 200
        assert resp_alias.json()["command_id"] == str(cmd_id)

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_task_status_not_found():
    missing_id = uuid.uuid4()
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

    async def override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/v2/tasks/{missing_id}")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    app.dependency_overrides.clear()
