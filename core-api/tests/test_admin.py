import pytest
from unittest.mock import patch, AsyncMock
from app.routers.admin import get_worker_status, trigger_manual_job, ManualJobRequest


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
