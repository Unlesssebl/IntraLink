"""Taskiq broker configuration with Redis backend and multi-queue support using Redis Streams."""

import os
from typing import Final, Optional

from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

# Queue capabilities
QUEUE_DEFAULT: Final[str] = "default"
QUEUE_RAG_COMPUTE: Final[str] = "rag_compute"
QUEUE_WINDOWS_EXEC: Final[str] = "windows_exec"

SUPPORTED_QUEUES: Final[tuple[str, ...]] = (
    QUEUE_DEFAULT,
    QUEUE_RAG_COMPUTE,
    QUEUE_WINDOWS_EXEC,
)

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def create_broker(
    redis_url: Optional[str] = None,
    queue_name: str = QUEUE_DEFAULT,
    consumer_group_name: str = "intralink_workers",
    additional_streams: Optional[dict[str, str | int]] = None,
) -> RedisStreamBroker:
    """Create a configured RedisStreamBroker instance with Redis result backend."""
    url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    # Invariants for resilient queue listening:
    # 1. socket_timeout=None prevents premature TimeoutError during idle stream listening
    # 2. socket_keepalive and health_check_interval keep long-lived connections open through NAT/Docker bridges
    redis_conn_kwargs = {
        "socket_timeout": None,
        "socket_connect_timeout": 10.0,
        "socket_keepalive": True,
        "health_check_interval": 15,
    }
    result_backend = RedisAsyncResultBackend(
        redis_url=url,
        **redis_conn_kwargs,
    )
    extra_streams = additional_streams
    if extra_streams is None and queue_name == QUEUE_DEFAULT:
        extra_streams = {QUEUE_RAG_COMPUTE: ">"}

    broker_instance = RedisStreamBroker(
        url=url,
        queue_name=queue_name,
        consumer_group_name=consumer_group_name,
        mkstream=True,
        xread_block=2000,
        unacknowledged_batch_size=100,
        unacknowledged_lock_timeout=30.0,
        additional_streams=extra_streams,
        **redis_conn_kwargs,
    )
    return broker_instance.with_result_backend(result_backend)


# Default shared broker instance
broker: RedisStreamBroker = create_broker(queue_name=QUEUE_DEFAULT)


def get_broker_for_queue(queue_name: str, redis_url: Optional[str] = None) -> RedisStreamBroker:
    """Return a broker bound to a specific capability queue."""
    if queue_name not in SUPPORTED_QUEUES:
        raise ValueError(f"Unsupported queue: '{queue_name}'. Must be one of {SUPPORTED_QUEUES}")
    return create_broker(redis_url=redis_url, queue_name=queue_name, additional_streams={})
