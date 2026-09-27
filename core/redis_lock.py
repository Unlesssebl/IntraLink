"""Distributed fail-closed token-based concurrency lease for Redis."""

import asyncio
import logging
import uuid
from typing import Any, Optional

import redis.asyncio as aioredis

logger = logging.getLogger("core.redis_lock")

_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

_RENEW_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class DistributedTaskLockOwnershipLost(RuntimeError):
    """Raised when a worker can no longer prove ownership of its task lease."""


class DistributedTaskLock:
    """Fail-closed, ownership-tokenized Redis lease with auto-renewal and atomic release."""

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis],
        key: str,
        ttl_seconds: int = 60,
    ) -> None:
        self.redis = redis_client
        self.key = key
        self.ttl = ttl_seconds
        self.token = str(uuid.uuid4())
        self.acquired = False
        self._renew_task: Optional[asyncio.Task] = None
        self._renewal_error: Optional[BaseException] = None

    async def acquire(self) -> bool:
        """Attempt to acquire distributed lock fail-closed. Returns False on any error or missing Redis."""
        if self.redis is None:
            logger.warning("DistributedTaskLock fail-closed: Redis client is unavailable for key '%s'", self.key)
            self.acquired = False
            return False

        try:
            res = await self.redis.set(self.key, self.token, nx=True, ex=self.ttl)
            self.acquired = bool(res)
            if self.acquired:
                self._renewal_error = None
                self._renew_task = asyncio.create_task(self._auto_renew())
            return self.acquired
        except Exception as exc:
            logger.warning("DistributedTaskLock acquire failed (fail-closed) for key '%s': %s", self.key, exc)
            self.acquired = False
            return False

    async def _auto_renew(self) -> None:
        """Background loop renewing lease while task is active."""
        renew_interval = max(1.0, self.ttl / 3.0)
        try:
            while self.acquired:
                await asyncio.sleep(renew_interval)
                if not self.acquired or self.redis is None:
                    break
                try:
                    renewed = await self.redis.eval(_RENEW_LUA, 1, self.key, self.token, self.ttl)
                    if not renewed:
                        logger.warning("DistributedTaskLock lease '%s' lost during renewal (token mismatch/expired)", self.key)
                        self.acquired = False
                        self._renewal_error = DistributedTaskLockOwnershipLost(
                            f"Distributed task lock ownership was lost for '{self.key}'."
                        )
                        break
                except Exception as exc:
                    logger.warning("DistributedTaskLock renewal failed for key '%s': %s", self.key, exc)
                    self.acquired = False
                    self._renewal_error = DistributedTaskLockOwnershipLost(
                        f"Distributed task lock renewal failed for '{self.key}'."
                    )
                    break
        except asyncio.CancelledError:
            pass

    async def ensure_owned(self) -> None:
        """Synchronously prove lease ownership before or after an irreversible action."""
        if self._renewal_error is not None:
            raise self._renewal_error
        if not self.acquired or self.redis is None:
            raise DistributedTaskLockOwnershipLost(
                f"Distributed task lock is not owned for '{self.key}'."
            )
        try:
            renewed = await self.redis.eval(_RENEW_LUA, 1, self.key, self.token, self.ttl)
        except Exception as exc:
            self.acquired = False
            self._renewal_error = DistributedTaskLockOwnershipLost(
                f"Distributed task lock ownership could not be verified for '{self.key}'."
            )
            raise self._renewal_error from exc
        if not renewed:
            self.acquired = False
            self._renewal_error = DistributedTaskLockOwnershipLost(
                f"Distributed task lock ownership was lost for '{self.key}'."
            )
            raise self._renewal_error

    async def release(self) -> bool:
        """Atomically release lock via compare-and-delete Lua script."""
        if self._renew_task:
            self._renew_task.cancel()
            self._renew_task = None

        if not self.acquired or self.redis is None:
            self.acquired = False
            return False

        self.acquired = False
        try:
            res = await self.redis.eval(_RELEASE_LUA, 1, self.key, self.token)
            return bool(res)
        except Exception as exc:
            logger.warning("DistributedTaskLock release error for key '%s': %s", self.key, exc)
            return False

    async def __aenter__(self) -> "DistributedTaskLock":
        ok = await self.acquire()
        if not ok:
            raise RuntimeError(f"Could not acquire distributed lock for '{self.key}' (fail-closed).")
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.release()
