"""Integration tests for Tickets vertical slice."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.src.main import app
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
