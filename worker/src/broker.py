"""Backward-compatibility shim for core.broker."""

from core.broker import (
    QUEUE_DEFAULT,
    QUEUE_RAG_COMPUTE,
    QUEUE_WINDOWS_EXEC,
    REDIS_URL,
    SUPPORTED_QUEUES,
    broker,
    create_broker,
    get_broker_for_queue,
)

__all__ = [
    "QUEUE_DEFAULT",
    "QUEUE_RAG_COMPUTE",
    "QUEUE_WINDOWS_EXEC",
    "REDIS_URL",
    "SUPPORTED_QUEUES",
    "broker",
    "create_broker",
    "get_broker_for_queue",
]
