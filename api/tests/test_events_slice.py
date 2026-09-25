import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.src.main import app


@pytest.mark.asyncio
async def test_events_stream_connected_and_retry():
    with patch("api.src.features.events.router.get_redis_client") as mock_get_redis:
        mock_r = MagicMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.punsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()
        mock_pubsub.get_message = AsyncMock(side_effect=asyncio.CancelledError)
        mock_r.pubsub.return_value = mock_pubsub
        mock_get_redis.return_value = mock_r

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Test v2 endpoint
            async with client.stream("GET", "/api/v2/events/stream") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                first_chunk = await anext(response.aiter_text())
                assert "retry: 3000" in first_chunk
                assert "event: connected" in first_chunk

            # Test backwards compatibility v1 alias
            async with client.stream("GET", "/api/v1/events/stream?channel=all") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                first_chunk = await anext(response.aiter_text())
                assert "retry: 3000" in first_chunk
                assert "event: connected" in first_chunk
