from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database.db import get_db
from app.main import app
from app.routers.deps import get_service_auth_b64

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


@pytest.fixture(autouse=True)
def override_deps():
    async def mock_get_service_auth_b64():
        return "bW9ja19hdXRoX2I2NA=="

    async def mock_get_db():
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        yield session

    app.dependency_overrides[get_service_auth_b64] = mock_get_service_auth_b64
    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_triage_services():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/triage/services", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(s["root_number"] == "01" for s in data)
        assert any(s["root_number"] == "03" for s in data)


@pytest.mark.asyncio
async def test_get_triage_templates():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/triage/templates", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "wifi_access" in data or "in_work_standard" in data


@pytest.mark.asyncio
async def test_session_skip_and_reset():
    with patch("app.routers.triage.get_redis_client") as mock_redis_func:
        mock_r = AsyncMock()
        mock_r.sadd = AsyncMock(return_value=1)
        mock_r.delete = AsyncMock(return_value=1)
        mock_redis_func.return_value = mock_r

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp_skip = await client.post(
                "/api/v1/triage/session/skip",
                headers=HEADERS,
                json={"task_ids": [100, 101], "reason": "skip_test"},
            )
            assert resp_skip.status_code == 200
            assert resp_skip.json()["status"] == "success"

            resp_reset = await client.post(
                "/api/v1/triage/session/reset", headers=HEADERS
            )
            assert resp_reset.status_code == 200
            assert resp_reset.json()["status"] == "success"


@pytest.mark.asyncio
async def test_get_triage_batch():
    mock_tasks = [
        {
            "Id": 139001,
            "Name": "Настроить Wi-Fi на телефоне",
            "Created": "2026-09-01T10:00:00",
            "StatusId": 26,
            "StatusName": "Новая",
            "ServiceId": 42,
            "ServiceName": "01. Учетные записи",
            "Creator": "Иванов И.И.",
            "CreatorPhone": "49-87",
            "_field_meta": {"phone": "49-87", "room": "112", "pc_name": "ZTE1234"},
        }
    ]

    with patch(
        "app.services.intraservice.get_tasks_by_filter",
        new_callable=AsyncMock,
        return_value=mock_tasks,
    ), patch(
        "app.routers.triage.get_skipped_task_ids",
        new_callable=AsyncMock,
        return_value=set(),
    ), patch(
        "app.routers.triage.search_knowledge_base",
        new_callable=AsyncMock,
        return_value=[],
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/triage/batch?limit=5", headers=HEADERS)
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_open"] == 1
            assert len(data["tasks"]) == 1
            assert data["tasks"][0]["task_id"] == 139001
            assert "suggested_action" in data["tasks"][0]


@pytest.mark.asyncio
async def test_apply_triage_action():
    with patch(
        "app.services.intraservice.update_task_full",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.intraservice.add_task_expenses",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.intraservice.get_single_task",
        new_callable=AsyncMock,
        return_value={"Id": 139001, "Name": "Тест", "Description": "Тест"},
    ), patch(
        "app.routers.triage.index_task_knowledge",
        new_callable=AsyncMock,
        return_value=True,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/triage/apply",
                headers=HEADERS,
                json={
                    "task_ids": [139001],
                    "status_id": 29,
                    "comment": "Выполнено",
                    "expenses": 10,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["results"]) == 1
            assert data["results"][0]["status"] == "success"


@pytest.mark.asyncio
async def test_rag_sync_endpoint():
    with patch(
        "app.routers.triage.sync_historical_closed_tasks",
        new_callable=AsyncMock,
        return_value={
            "status": "success",
            "total_fetched": 10,
            "total_closed": 5,
            "indexed": 3,
            "skipped": 2,
        },
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/triage/rag/sync",
                headers=HEADERS,
                json={"days": 14, "limit": 20},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["indexed"] == 3

