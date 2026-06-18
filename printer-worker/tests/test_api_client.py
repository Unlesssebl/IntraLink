import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import aiohttp

# Устанавливаем переменные окружения до импорта модулей воркера
os.environ["BOT_API_KEY"] = "dummy_key"
os.environ["CORE_API_URL"] = "http://localhost:8000"

import worker_services.api_client as api_client


@pytest.fixture(autouse=True)
async def cleanup_session():
    # Гарантируем очистку сессии после каждого теста
    await api_client.close_session()
    yield
    await api_client.close_session()


@pytest.mark.asyncio
async def test_session_lifecycle():
    assert api_client._session is None

    session = await api_client.get_session()
    assert session is not None
    assert not session.closed
    assert api_client._session is session

    await api_client.close_session()
    assert api_client._session is None


@pytest.mark.asyncio
async def test_make_request_success():
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {"status": "ok"}

    mock_request = MagicMock()
    mock_request.__aenter__.return_value = mock_response

    session = await api_client.get_session()
    with patch.object(session, "request", return_value=mock_request) as mock_req_method:
        res = await api_client._make_request(
            "test-endpoint", method="GET", params={"param": 1}
        )
        assert res == {"status": "ok"}
        mock_req_method.assert_called_once_with(
            method="GET",
            url=f"{api_client.CORE_API_URL.rstrip('/')}/test-endpoint",
            headers={
                "Content-Type": "application/json",
                "X-Bot-Api-Key": api_client.BOT_API_KEY,
            },
            params={"param": 1},
            json=None,
            timeout=aiohttp.ClientTimeout(total=20),
        )


@pytest.mark.asyncio
async def test_make_request_error_status():
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.text.return_value = "Internal Server Error"

    mock_request = MagicMock()
    mock_request.__aenter__.return_value = mock_response

    session = await api_client.get_session()
    with patch.object(session, "request", return_value=mock_request):
        res = await api_client._make_request("test-endpoint")
        assert res is None


@pytest.mark.asyncio
async def test_make_request_exception():
    session = await api_client.get_session()
    with patch.object(session, "request", side_effect=Exception("Connection refused")):
        res = await api_client._make_request("test-endpoint")
        assert res is None


@pytest.mark.asyncio
async def test_get_task_details():
    with patch(
        "worker_services.api_client._make_request", new_callable=AsyncMock
    ) as mock_make:
        mock_make.return_value = {"id": 123, "name": "Task name"}
        res = await api_client.get_task_details(tg_user_id=555, task_id=123)
        assert res == {"id": 123, "name": "Task name"}
        mock_make.assert_called_once_with(endpoint="service/tasks/123", method="GET")


@pytest.mark.asyncio
async def test_add_task_comment():
    with patch(
        "worker_services.api_client._make_request", new_callable=AsyncMock
    ) as mock_make:
        mock_make.return_value = {"status": "created"}
        res = await api_client.add_task_comment(
            tg_user_id=555, task_id=123, comment="Hello"
        )
        assert res is True
        mock_make.assert_called_once_with(
            endpoint="service/tasks/123/comment",
            method="POST",
            json_data={"comment": "Hello"},
        )


@pytest.mark.asyncio
async def test_update_task_status():
    with patch(
        "worker_services.api_client._make_request", new_callable=AsyncMock
    ) as mock_make:
        mock_make.return_value = {"status": "updated"}
        res = await api_client.update_task_status(
            tg_user_id=555, task_id=123, status_id=29
        )
        assert res is True
        mock_make.assert_called_once_with(
            endpoint="service/tasks/123/status",
            method="POST",
            json_data={"status_id": 29},
        )


@pytest.mark.asyncio
async def test_add_task_expenses():
    with patch(
        "worker_services.api_client._make_request", new_callable=AsyncMock
    ) as mock_make:
        mock_make.return_value = {"status": "added"}
        res = await api_client.add_task_expenses(
            tg_user_id=555, task_id=123, minutes=30
        )
        assert res is True
        mock_make.assert_called_once_with(
            endpoint="service/tasks/123/expenses",
            method="POST",
            json_data={"minutes": 30},
        )
