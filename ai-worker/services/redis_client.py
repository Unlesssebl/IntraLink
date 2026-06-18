import redis.asyncio as aioredis
from core.config import settings

redis_client = None


def get_redis_client():
    global redis_client
    if redis_client is None:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return redis_client


async def close_redis():
    global redis_client
    if redis_client is not None:
        await redis_client.close()
        redis_client = None
