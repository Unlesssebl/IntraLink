"""Unit tests for Two-tier EmbeddingCache (L1 In-Memory LRU + L2 Redis)."""

import json
from unittest.mock import AsyncMock

import pytest

from core.rag.embed_cache import EmbeddingCache, InMemoryLRUCache
from core.rag.embedder import get_embedding_vector


def test_in_memory_lru_eviction():
    """Verify L1 LRU correctly maintains capacity and evicts oldest items."""
    cache = InMemoryLRUCache(maxsize=3)

    cache.set("k1", [1.0, 0.0])
    cache.set("k2", [0.0, 1.0])
    cache.set("k3", [0.5, 0.5])
    assert len(cache) == 3

    # Access k1 so k2 becomes the oldest
    assert cache.get("k1") == [1.0, 0.0]

    # Add k4, k2 must be evicted
    cache.set("k4", [0.2, 0.8])
    assert len(cache) == 3
    assert cache.get("k2") is None
    assert cache.get("k1") == [1.0, 0.0]
    assert cache.get("k3") == [0.5, 0.5]
    assert cache.get("k4") == [0.2, 0.8]


@pytest.mark.asyncio
async def test_embedding_cache_l1_hit():
    """Verify L1 in-memory hit does not invoke Redis or AI client."""
    mock_redis = AsyncMock()
    cache = EmbeddingCache(in_memory_maxsize=10, redis_client=mock_redis)

    vec = [0.1, 0.2, 0.3]
    await cache.set("принтер не печатает", vec, "bge-m3")

    # Redis set was called once on write
    assert mock_redis.set.await_count == 1

    # Read back - should hit L1 memory, Redis get should NOT be called
    hit = await cache.get("принтер не печатает", "bge-m3")
    assert hit == vec
    assert mock_redis.get.await_count == 0


@pytest.mark.asyncio
async def test_embedding_cache_l2_redis_hit_and_l1_hydration():
    """Verify missing L1 falls back to L2 Redis and hydrates L1."""
    mock_redis = AsyncMock()
    cache = EmbeddingCache(in_memory_maxsize=10, redis_client=mock_redis)

    vec = [0.9, 0.8, 0.7]
    mock_redis.get.return_value = json.dumps(vec)

    # First get - misses L1, reads Redis
    res = await cache.get("сброс пароля", "bge-m3")
    assert res == vec
    assert mock_redis.get.await_count == 1

    # Second get - should now be cached in L1! Redis should not be queried again
    res2 = await cache.get("сброс пароля", "bge-m3")
    assert res2 == vec
    assert mock_redis.get.await_count == 1


@pytest.mark.asyncio
async def test_embedder_uses_cache():
    """Verify get_embedding_vector transparently checks and updates cache."""
    from unittest.mock import MagicMock

    mock_ai = AsyncMock()
    mock_resp = MagicMock()
    mock_item = MagicMock()
    mock_item.embedding = [0.42] * 1024
    mock_resp.data = [mock_item]
    mock_ai.embeddings.create.return_value = mock_resp

    test_cache = EmbeddingCache(in_memory_maxsize=10, redis_client=None)

    # First call - calls AI client
    vec1 = await get_embedding_vector("тестовый запрос", mock_ai, use_cache=False)
    assert vec1 == [0.42] * 1024
    assert mock_ai.embeddings.create.await_count == 1

    # Call with cache
    from core.rag.embed_cache import set_embedding_cache
    set_embedding_cache(test_cache)

    vec2 = await get_embedding_vector("запрос с кэшем", mock_ai, use_cache=True)
    assert vec2 == [0.42] * 1024
    assert mock_ai.embeddings.create.await_count == 2

    # Second call with same text - cached in L1, AI client NOT called!
    vec3 = await get_embedding_vector("запрос с кэшем", mock_ai, use_cache=True)
    assert vec3 == [0.42] * 1024
    assert mock_ai.embeddings.create.await_count == 2  # Remains 2!
