"""Asynchronous Redis client management for core and worker components."""

import logging
import os
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger("core.redis")

_redis_client: Optional[aioredis.Redis] = None


def get_redis_client(redis_url: Optional[str] = None) -> aioredis.Redis:
    """Return the shared asynchronous Redis client instance."""
    global _redis_client
    if _redis_client is None:
        url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = aioredis.from_url(
            url,
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
        )
    return _redis_client


def set_redis_client(client: Optional[aioredis.Redis]) -> None:
    """Override Redis client for unit testing."""
    global _redis_client
    _redis_client = client


async def close_redis() -> None:
    """Close active Redis client connection pool."""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception as exc:
            logger.debug("Error while closing Redis client: %s", exc)
        finally:
            _redis_client = None
