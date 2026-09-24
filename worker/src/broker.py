"""Taskiq broker configuration with Redis backend and multi-queue support."""

import os
from typing import Final, Optional

from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

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
) -> ListQueueBroker:
    """Create a configured ListQueueBroker instance with Redis result backend."""
    url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
    result_backend = RedisAsyncResultBackend(redis_url=url)
    broker_instance = ListQueueBroker(
        url=url,
        queue_name=queue_name,
    )
    return broker_instance.with_result_backend(result_backend)


# Default worker broker instance
broker: ListQueueBroker = create_broker(queue_name=QUEUE_DEFAULT)


def get_broker_for_queue(queue_name: str, redis_url: Optional[str] = None) -> ListQueueBroker:
    """Return a broker bound to a specific capability queue."""
    if queue_name not in SUPPORTED_QUEUES:
        raise ValueError(f"Unsupported queue: '{queue_name}'. Must be one of {SUPPORTED_QUEUES}")
    return create_broker(redis_url=redis_url, queue_name=queue_name)
