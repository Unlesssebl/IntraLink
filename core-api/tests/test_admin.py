import pytest
from unittest.mock import patch, AsyncMock
from app.routers.admin import (
    get_system_status,
    set_service_user,
    delete_service_user,
    get_worker_logs,
    ServiceUserRequest,
)


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
async def test_get_system_status_endpoint(mock_get_redis):
    """
    Проверяет получение статуса системы.
    """
    mock_redis = AsyncMock()
    mock_redis.ping.return_value = True
    mock_redis.get.return_value = b"encrypted_token"
    mock_get_redis.return_value = mock_redis

    res = await get_system_status()
    assert "status" in res
    assert res["redis_connected"] is True
    assert res["service_user_configured"] is True


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
@patch("app.routers.admin.verify_credentials")
async def test_set_and_delete_service_user(mock_verify, mock_get_redis):
    """
    Проверяет сохранение и удаление сервисного аккаунта.
    """
    mock_verify.return_value = ("YXV0aA==", 123)
    mock_redis = AsyncMock()
    mock_get_redis.return_value = mock_redis

    req = ServiceUserRequest(login="admin", password="password")
    res = await set_service_user(req)
    assert res["status"] == "success"
    assert res["login"] == "admin"
    mock_redis.set.assert_awaited_once()

    del_res = await delete_service_user()
    assert del_res["status"] == "success"
    mock_redis.delete.assert_awaited_once_with("worker:service_auth_b64")


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
async def test_get_worker_logs_endpoint(mock_get_redis):
    """
    Проверяет считывание логов воркера из stream:intraservice_events.
    """
    mock_redis = AsyncMock()
    mock_redis.xrevrange.return_value = [
        ("1700000000000-0", {"event_type": "new_task", "task_id": "100", "text": "Task #100 created"}),
    ]
    mock_get_redis.return_value = mock_redis

    res = await get_worker_logs()
    assert res["total"] == 1
    assert res["logs"][0]["task_id"] == "100"
