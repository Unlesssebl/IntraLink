import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database.db import JobLog, get_db
from app.main import app

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


@pytest.fixture(autouse=True)
def override_deps():
    async def mock_get_db():
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.add = MagicMock()
        yield session

    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_submit_command_auto():
    with patch("app.routers.commands.get_redis_client") as mock_redis_func:
        mock_r = AsyncMock()
        mock_r.set = AsyncMock(return_value=True)
        mock_r.xadd = AsyncMock(return_value="1725180000-0")
        mock_r.publish = AsyncMock(return_value=1)
        mock_redis_func.return_value = mock_r

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/commands",
                headers=HEADERS,
                json={
                    "type": "grant_wlan",
                    "target": {"identity": "test.user", "task_id": 139099},
                    "params": {"login": "test.user"},
                    "mode": "auto",
                    "priority": 7,
                },
            )
            assert resp.status_code == 202
            data = resp.json()
            assert data["status"] == "accepted"
            assert data["command_type"] == "grant_wlan"
            assert data["job_id"].startswith("job_")
            assert data["task_id"] == 139099


@pytest.mark.asyncio
async def test_submit_command_dry_run():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/commands",
            headers=HEADERS,
            json={
                "type": "create_user",
                "target": {"task_id": 139100},
                "params": {"surname": "Иванов", "name": "Иван"},
                "mode": "dry_run",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "dry_run_success"
        assert data["command_type"] == "create_user"
        assert "Симуляция" in data["message"]


@pytest.mark.asyncio
async def test_get_command_status():
    with patch("app.routers.commands.get_redis_client") as mock_redis_func:
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(
            return_value=json.dumps(
                {
                    "job_id": "job_abc123",
                    "action": "grant_wlan",
                    "status": "success",
                    "message": "Доступ предоставлен",
                }
            )
        )
        mock_redis_func.return_value = mock_r

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/commands/job_abc123", headers=HEADERS)
            assert resp.status_code == 200
            data = resp.json()
            assert data["job_id"] == "job_abc123"
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_confirm_command_approve():
    with patch("app.routers.commands.get_redis_client") as mock_redis_func:
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(
            return_value=json.dumps(
                {
                    "job_id": "job_hitl1",
                    "status": "confirm_required",
                    "action": "grant_wlan",
                }
            )
        )
        mock_r.lpush = AsyncMock(return_value=1)
        mock_r.set = AsyncMock(return_value=True)
        mock_r.publish = AsyncMock(return_value=1)
        mock_redis_func.return_value = mock_r

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/commands/job_hitl1/confirm",
                headers=HEADERS,
                json={"decision": "approve", "operator": "belikov.a"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "decision_recorded"
            assert data["decision"] == "approve"


@pytest.mark.asyncio
async def test_cancel_command():
    with patch("app.routers.commands.get_redis_client") as mock_redis_func:
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(
            return_value=json.dumps(
                {
                    "job_id": "job_cancel1",
                    "status": "queued",
                    "action": "install_printer",
                }
            )
        )
        mock_r.set = AsyncMock(return_value=True)
        mock_r.lpush = AsyncMock(return_value=1)
        mock_r.publish = AsyncMock(return_value=1)
        mock_redis_func.return_value = mock_r

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/commands/job_cancel1/cancel",
                headers=HEADERS,
                params={"reason": "Ошибка в номере порта"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "cancelled"
            assert data["reason"] == "Ошибка в номере порта"
