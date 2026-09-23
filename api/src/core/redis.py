"""Redis asynchronous connection pool and client lifecycle."""

import logging
from typing import AsyncGenerator, Optional

import redis.asyncio as aioredis

from api.src.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[aioredis.Redis] = None


def get_redis_client() -> aioredis.Redis:
    """Return the global async Redis client instance."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
    return _redis_client


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """FastAPI dependency for accessing Redis."""
    client = get_redis_client()
    yield client


async def check_redis_health() -> bool:
    """Check Redis connectivity via PING."""
    try:
        client = get_redis_client()
        return await client.ping()
    except Exception as exc:
        logger.warning(f"Redis health check failed: {exc}")
        return False


async def close_redis() -> None:
    """Close Redis connection pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
