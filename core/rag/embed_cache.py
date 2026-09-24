"""Two-tier embedding cache for RAG operations (In-Memory LRU + Redis L2).

Design invariants:
1. Embeddings are deterministic for a given model: embed(text, model) is constant.
2. L1: Fast thread-safe In-Memory LRU (collections.OrderedDict + threading.Lock).
   Zero allocations, zero network latency (~0 ms). Max 2048 vectors (~8 MB RAM).
3. L2: Redis shared cross-process cache (TTL 7 days default) for synchronizing
   between intralink_api and intralink_worker containers.
4. Resilient & graceful degradation: If Redis is unavailable or fails,
   L1 cache operates independently without interrupting application flow.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from typing import Any, List, Optional

logger = logging.getLogger("core.rag.embed_cache")


class InMemoryLRUCache:
    """Thread-safe bounded in-memory LRU cache based on collections.OrderedDict."""

    def __init__(self, maxsize: int = 2048) -> None:
        self.maxsize = maxsize
        self._cache: OrderedDict[str, List[float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[List[float]]:
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            return self._cache[key]

    def set(self, key: str, value: List[float]) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            if len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


class EmbeddingCache:
    """Two-tier embedding cache (L1 In-Memory LRU, L2 Redis)."""

    def __init__(
        self,
        in_memory_maxsize: int = 2048,
        redis_client: Optional[Any] = None,
        ttl_seconds: int = 7 * 86400,
    ) -> None:
        self._l1 = InMemoryLRUCache(maxsize=in_memory_maxsize)
        self._redis_client = redis_client
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _compute_key(text: str, model_name: str) -> str:
        """Compute deterministic SHA-256 hash for model and input text."""
        normalized = text.strip()
        hasher = hashlib.sha256()
        hasher.update(model_name.encode("utf-8"))
        hasher.update(b":")
        hasher.update(normalized.encode("utf-8"))
        return hasher.hexdigest()

    def _redis_key(self, model_name: str, hash_key: str) -> str:
        return f"rag:emb:{model_name}:{hash_key}"

    def _get_redis(self) -> Optional[Any]:
        if self._redis_client is not None:
            return self._redis_client
        try:
            from core.redis_client import get_redis_client

            return get_redis_client()
        except Exception as exc:
            logger.debug("Failed to acquire Redis client for EmbeddingCache: %s", exc)
            return None

    async def get(self, text: str, model_name: str) -> Optional[List[float]]:
        """Retrieve embedding vector from L1 RAM or L2 Redis."""
        if not text or not text.strip():
            return None

        hash_key = self._compute_key(text, model_name)

        # 1. Check L1 In-Memory LRU (0 ms)
        l1_vector = self._l1.get(hash_key)
        if l1_vector is not None:
            return l1_vector

        # 2. Check L2 Redis (~1 ms)
        redis = self._get_redis()
        if redis is not None:
            redis_k = self._redis_key(model_name, hash_key)
            try:
                raw_json = await redis.get(redis_k)
                if raw_json is not None:
                    vector = json.loads(raw_json)
                    if isinstance(vector, list) and vector:
                        # Populate back to L1
                        self._l1.set(hash_key, vector)
                        return vector
            except Exception as exc:
                logger.debug("Redis L2 cache read error: %s", exc)

        return None

    async def set(self, text: str, vector: List[float], model_name: str) -> None:
        """Store embedding vector into both L1 RAM and L2 Redis."""
        if not text or not text.strip() or not vector:
            return

        hash_key = self._compute_key(text, model_name)

        # 1. Store in L1
        self._l1.set(hash_key, vector)

        # 2. Store in L2 Redis
        redis = self._get_redis()
        if redis is not None:
            redis_k = self._redis_key(model_name, hash_key)
            try:
                await redis.set(redis_k, json.dumps(vector), ex=self._ttl_seconds)
            except Exception as exc:
                logger.debug("Redis L2 cache write error: %s", exc)

    def clear_memory(self) -> None:
        """Flush in-memory L1 cache."""
        self._l1.clear()


_global_embedding_cache: Optional[EmbeddingCache] = None


def get_embedding_cache() -> EmbeddingCache:
    """Return shared EmbeddingCache singleton."""
    global _global_embedding_cache
    if _global_embedding_cache is None:
        _global_embedding_cache = EmbeddingCache()
    return _global_embedding_cache


def set_embedding_cache(cache: Optional[EmbeddingCache]) -> None:
    """Override shared EmbeddingCache singleton (useful for testing)."""
    global _global_embedding_cache
    _global_embedding_cache = cache
