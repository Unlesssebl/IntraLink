"""Redis asynchronous connection pool and client lifecycle for API."""

import logging
from typing import AsyncGenerator

import redis.asyncio as aioredis

from api.src.core.config import settings
from core.redis_client import close_redis as _core_close_redis
from core.redis_client import get_redis_client as _core_get_redis_client

logger = logging.getLogger(__name__)


def get_redis_client() -> aioredis.Redis:
    """Return the global async Redis client instance."""
    return _core_get_redis_client(redis_url=settings.REDIS_URL)


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
    await _core_close_redis()
