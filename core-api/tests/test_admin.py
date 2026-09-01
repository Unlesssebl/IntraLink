import pytest
from unittest.mock import patch, AsyncMock
from app.routers.admin import (
    get_worker_status,
    trigger_manual_job,
    ManualJobRequest,
    delete_print_job,
)


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
async def test_get_worker_status_online(mock_get_redis):
    """
    Проверяет get_worker_status когда воркер в сети.
    """
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "online"
    mock_get_redis.return_value = mock_redis

    response = await get_worker_status()
    assert response == {"status": "online"}
    mock_redis.get.assert_called_once_with("printer_worker:status")


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
async def test_get_worker_status_offline(mock_get_redis):
    """
    Проверяет get_worker_status когда воркер оффлайн.
    """
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_get_redis.return_value = mock_redis

    response = await get_worker_status()
    assert response == {"status": "offline"}


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
async def test_trigger_manual_job(mock_get_redis):
    """
    Проверяет успешный запуск ручной задачи установки принтера.
    """
    mock_redis = AsyncMock()
    mock_redis.publish.return_value = 1
    mock_get_redis.return_value = mock_redis

    req = ManualJobRequest(
        target_pc="WS-TEST-100",
        model_key="kyocera_m2040",
        connection_type="tcpip",
        printer_address="192.168.1.55",
    )

    response = await trigger_manual_job(req)
    assert response["status"] == "success"
    assert "task_id" in response
    assert mock_redis.publish.called


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
async def test_delete_print_job(mock_get_redis):
    """
    Проверяет успешное удаление задачи и логов из Redis.
    """
    mock_redis = AsyncMock()
    mock_redis.zrem.return_value = 1
    mock_redis.delete.return_value = 1
    mock_get_redis.return_value = mock_redis

    response = await delete_print_job(99999)
    assert response == {"status": "success", "task_id": 99999}
    mock_redis.zrem.assert_called_once_with("printer_jobs_list", "99999")
    assert mock_redis.delete.call_count == 2
    mock_redis.delete.assert_any_call("printer_job:99999")
    mock_redis.delete.assert_any_call("printer_job_logs_history:99999")


@pytest.mark.asyncio
@patch("app.routers.admin.get_redis_client")
async def test_get_system_status_endpoint(mock_get_redis):
    """
    Проверяет получение статуса системы.
    """
    from app.routers.admin import get_system_status

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
    from app.routers.admin import set_service_user, delete_service_user, ServiceUserRequest

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
    from app.routers.admin import get_worker_logs

    mock_redis = AsyncMock()
    mock_redis.xrevrange.return_value = [
        ("1700000000000-0", {"event_type": "new_task", "task_id": "100", "text": "Task #100 created"}),
    ]
    mock_get_redis.return_value = mock_redis

    res = await get_worker_logs()
    assert res["total"] == 1
    assert res["logs"][0]["task_id"] == "100"

