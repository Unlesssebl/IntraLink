import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


@pytest.mark.asyncio
async def test_events_stream_connected():
    with patch("app.routers.events.get_redis_client") as mock_redis_func:
        mock_r = MagicMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.punsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()

        # Завершаем генератор после приветственного SSE-события. Мгновенный
        # return None создаёт бесконечный цикл и не моделирует реальный таймаут.
        mock_pubsub.get_message = AsyncMock(side_effect=asyncio.CancelledError)
        mock_r.pubsub.return_value = mock_pubsub
        mock_redis_func.return_value = mock_r

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            async with client.stream(
                "GET", "/api/v1/events/stream?job_id=job_test123", headers=HEADERS
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")

                # Читаем первый чанк
                async for chunk in response.aiter_text():
                    if "event: connected" in chunk:
                        assert "job_test123" in chunk
                        break
