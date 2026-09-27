"""Fail-closed Redis lease used to serialize ticket analysis."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import Any, Optional

_RENEW_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""

_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class AnalysisLeaseError(RuntimeError):
    """Base class for controlled single-flight failures."""


class AnalysisLeaseBackendError(AnalysisLeaseError):
    """Redis could not establish or confirm the lease."""


class AnalysisLeaseOwnershipLost(AnalysisLeaseError):
    """The lease token no longer owns the Redis key."""


class RedisAnalysisLease:
    """Ownership-token Redis lease with periodic compare-and-expire renewal."""

    def __init__(
        self,
        redis_client: Any,
        key: str,
        *,
        ttl_seconds: int = 60,
        renewal_interval_seconds: Optional[float] = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._redis = redis_client
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.renewal_interval_seconds = renewal_interval_seconds or max(0.1, ttl_seconds / 3)
        self.owner_token = uuid.uuid4().hex
        self._renewal_task: Optional[asyncio.Task[None]] = None
        self._renewal_error: Optional[AnalysisLeaseError] = None
        self._acquired = False

    async def acquire(self) -> bool:
        if self._redis is None:
            raise AnalysisLeaseBackendError("Redis is unavailable; analysis lease cannot be acquired")
        try:
            acquired = bool(
                await self._redis.set(
                    self.key,
                    self.owner_token,
                    nx=True,
                    ex=self.ttl_seconds,
                )
            )
        except Exception as exc:
            raise AnalysisLeaseBackendError("Redis SET NX EX failed for analysis lease") from exc
        self._acquired = acquired
        return acquired

    def start_renewal(self) -> None:
        if not self._acquired:
            raise AnalysisLeaseOwnershipLost("Cannot renew a lease that is not owned")
        if self._renewal_task is None:
            self._renewal_task = asyncio.create_task(self._renew_loop())

    async def renew(self) -> None:
        if not self._acquired:
            raise AnalysisLeaseOwnershipLost("Analysis lease is not owned")
        try:
            renewed = await self._redis.eval(
                _RENEW_LUA,
                1,
                self.key,
                self.owner_token,
                self.ttl_seconds,
            )
        except Exception as exc:
            raise AnalysisLeaseBackendError("Redis lease renewal failed") from exc
        if not renewed:
            self._acquired = False
            raise AnalysisLeaseOwnershipLost("Analysis lease ownership was lost")

    async def ensure_owned(self) -> None:
        if self._renewal_error is not None:
            raise self._renewal_error
        await self.renew()

    async def stop_renewal(self) -> None:
        if self._renewal_task is None:
            return
        self._renewal_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._renewal_task
        self._renewal_task = None

    async def release(self) -> bool:
        if not self._acquired:
            return False
        try:
            released = bool(
                await self._redis.eval(
                    _RELEASE_LUA,
                    1,
                    self.key,
                    self.owner_token,
                )
            )
        except Exception as exc:
            raise AnalysisLeaseBackendError("Redis compare-and-delete failed for analysis lease") from exc
        self._acquired = False
        return released

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self.renewal_interval_seconds)
            try:
                await self.renew()
            except AnalysisLeaseError as exc:
                self._renewal_error = exc
                return
