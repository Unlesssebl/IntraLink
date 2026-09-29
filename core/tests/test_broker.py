"""Tests for Taskiq RedisStreamBroker configuration and multi-queue capabilities."""

import pytest
from taskiq_redis import RedisStreamBroker

from core.broker import (
    QUEUE_DEFAULT,
    QUEUE_RAG_COMPUTE,
    SUPPORTED_QUEUES,
    broker,
    get_broker_for_queue,
)


def test_default_broker_is_redis_stream_broker():
    """Verify shared broker uses RedisStreamBroker with resilience parameters."""
    assert isinstance(broker, RedisStreamBroker)
    assert broker.queue_name == QUEUE_DEFAULT
    assert broker.consumer_group_name == "intralink_workers"
    assert broker.mkstream is True
    assert broker.block == 2000
    assert broker.unacknowledged_batch_size == 100
    assert broker.unacknowledged_lock_timeout == 30.0
    assert QUEUE_RAG_COMPUTE in broker.additional_streams


def test_get_broker_for_queue_supported_queues():
    """Verify factory returns dedicated stream brokers for supported capability queues."""
    for queue_name in SUPPORTED_QUEUES:
        q_broker = get_broker_for_queue(queue_name)
        assert isinstance(q_broker, RedisStreamBroker)
        assert q_broker.queue_name == queue_name


def test_get_broker_for_queue_unsupported_raises():
    """Verify factory raises ValueError for invalid queue names."""
    with pytest.raises(ValueError, match="Unsupported queue: 'invalid_queue'"):
        get_broker_for_queue("invalid_queue")
