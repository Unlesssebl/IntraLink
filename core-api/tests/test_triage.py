from unittest.mock import AsyncMock, MagicMock, patch
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
        session.scalar = AsyncMock(return_value=None)
        scalar_result = MagicMock()
        scalar_result.all.return_value = []
        session.scalars = AsyncMock(return_value=scalar_result)
        session.add = MagicMock()
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


@pytest.mark.asyncio
async def test_analyze_batch_endpoint():
    redis_store = {}
    mock_r = AsyncMock()

    async def mock_get(key):
        return redis_store.get(key)

    async def mock_set(key, val, **kwargs):
        redis_store[key] = str(val)
        return True

    async def mock_hget(key, field):
        h = redis_store.get(key, {})
        return h.get(field)

    async def mock_hset(key, field=None, value=None, mapping=None):
        if key not in redis_store or not isinstance(redis_store[key], dict):
            redis_store[key] = {}
        if mapping:
            for k, v in mapping.items():
                redis_store[key][k] = str(v)
        if field is not None:
            redis_store[key][field] = str(value)
        return 1

    async def mock_hgetall(key):
        return redis_store.get(key, {})

    async def mock_smembers(key):
        return set()

    async def mock_delete(*keys):
        for k in keys:
            redis_store.pop(k, None)
        return 1

    async def mock_expire(key, time):
        return True

    async def mock_publish(channel, message):
        return 1

    mock_r.get.side_effect = mock_get
    mock_r.set.side_effect = mock_set
    mock_r.hget.side_effect = mock_hget
    mock_r.hset.side_effect = mock_hset
    mock_r.hgetall.side_effect = mock_hgetall
    mock_r.smembers.side_effect = mock_smembers
    mock_r.delete.side_effect = mock_delete
    mock_r.expire.side_effect = mock_expire
    mock_r.publish.side_effect = mock_publish

    with patch("app.routers.triage.get_redis_client", return_value=mock_r), \
         patch("app.routers.triage._execute_triage_batch_worker", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/triage/analyze-batch",
                headers=HEADERS,
                json={"task_ids": list(range(1, 201))},
            )
            assert resp.status_code == 202
            data = resp.json()
            assert data["status"] == "accepted"
            assert data["total"] == 200
            assert "batch_id" in data
            assert data["already_running"] is False

            batch_id = data["batch_id"]

            # Проверка Single Flight: повторный запрос возвращает тот же активный батч
            resp_dup = await client.post(
                "/api/v1/triage/analyze-batch",
                headers=HEADERS,
                json={"task_ids": [1, 2, 3]},
            )
            assert resp_dup.status_code == 200
            data_dup = resp_dup.json()
            assert data_dup["status"] == "already_running"
            assert data_dup["batch_id"] == batch_id
            assert data_dup["already_running"] is True

            # Проверка получения статуса батча
            resp_status = await client.get(
                f"/api/v1/triage/analyze-batch/{batch_id}",
                headers=HEADERS,
            )
            assert resp_status.status_code == 200
            status_data = resp_status.json()
            assert status_data["batch_id"] == batch_id
            assert "status" in status_data

            # Проверка отмены батча
            resp_cancel = await client.post(
                f"/api/v1/triage/analyze-batch/{batch_id}/cancel",
                headers=HEADERS,
            )
            assert resp_cancel.status_code == 200
            cancel_data = resp_cancel.json()
            assert cancel_data["status"] == "cancelling"



