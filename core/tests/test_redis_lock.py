"""Fail-closed ownership tests for the shared Redis task lease."""

import pytest

from core.redis_lock import DistributedTaskLock, DistributedTaskLockOwnershipLost


class LeaseRedis:
    def __init__(self, *, renew_result=1, renew_error: Exception | None = None) -> None:
        self.value = None
        self.renew_result = renew_result
        self.renew_error = renew_error

    async def set(self, key, value, *, nx=False, ex=None):
        del key, nx, ex
        self.value = value
        return True

    async def eval(self, script, numkeys, key, *args):
        del numkeys, key
        if "expire" in script.lower():
            if self.renew_error is not None:
                raise self.renew_error
            return self.renew_result
        if args and self.value == args[0]:
            self.value = None
            return 1
        return 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("renew_result", "renew_error"),
    [(0, None), (1, RuntimeError("redis unavailable"))],
)
async def test_ensure_owned_fails_closed_when_lease_cannot_be_proven(renew_result, renew_error):
    redis = LeaseRedis(renew_result=renew_result, renew_error=renew_error)
    lock = DistributedTaskLock(redis, "lock:task:42")
    assert await lock.acquire() is True

    with pytest.raises(DistributedTaskLockOwnershipLost):
        await lock.ensure_owned()

    assert lock.acquired is False
    await lock.release()
