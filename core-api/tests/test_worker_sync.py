import pytest
from unittest.mock import AsyncMock, patch
import json


@pytest.mark.asyncio
async def test_sync_service_catalog():
    from app.services.worker import sync_service_catalog

    mock_services = [
        {
            "Id": 63,
            "Name": "Создание электронной почты",
            "ParentId": 42,
            "Path": "42|63|",
        },
        {
            "Id": 70,
            "Name": "Разблокировка электронной почты",
            "ParentId": 50,
            "Path": "50|70|",
        },
    ]

    with (
        patch("app.services.worker.get_redis_client") as mock_redis_func,
        patch(
            "app.services.worker.get_services", new_callable=AsyncMock
        ) as mock_get_services,
    ):
        mock_redis = AsyncMock()
        mock_redis_func.return_value = mock_redis
        mock_get_services.return_value = mock_services

        # Симулируем учетные данные
        mock_redis.get = AsyncMock(return_value="ZW5jcnlwdGVkX2F1dGg=")  # base64

        await sync_service_catalog()

        mock_get_services.assert_awaited_once()
        mock_redis.set.assert_awaited_once()

        # Проверяем, что в Redis записались правильные данные
        args, kwargs = mock_redis.set.call_args
        assert args[0] == "worker:service_catalog"
        catalog_data = json.loads(args[1])
        assert len(catalog_data) == 2
        assert catalog_data[0]["id"] == 63
        assert catalog_data[1]["name"] == "Разблокировка электронной почты"
