"""
Фоновый LeaseRenewer для защиты от Martin Kleppmann Fencing Problem ("Зомби-воркер").
Периодически продлевает lease команды в Core API и блокировку хоста в Redis.
При потере владения немедленно выставляет cancellation_token (Best-effort Cooperative Cancellation).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("execution_worker.sdk.lease_renewer")

# Lua-скрипт для атомарного продления блокировки хоста в Redis:
# Продлевает ключ только если его текущее значение совпадает с lock_token.
EXTEND_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class LeaseRenewer:
    """
    Фоновый менеджер продления lease и distributed lock во время выполнения фаз.
    """

    def __init__(
        self,
        command_id: str,
        worker_id: str,
        claim_token: str,
        cancellation_token: asyncio.Event,
        *,
        api_client: Any = None,
        core_api: Any = None,
        redis: Any = None,
        host_lock_key: str | None = None,
        host_lock_token: str | None = None,
        lease_seconds: int = 120,
        interval_seconds: float = 10.0,
    ) -> None:
        self.command_id = command_id
        self.worker_id = worker_id
        self.claim_token = claim_token
        self.cancellation_token = cancellation_token
        self.api_client = api_client or core_api
        self.redis = redis
        self.host_lock_key = host_lock_key
        self.host_lock_token = host_lock_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds

        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._consecutive_failures = 0

    async def start(self) -> None:
        """Запускает фоновый цикл продления."""
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Останавливает фоновый цикл продления."""
        self._stopped.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def __aenter__(self) -> LeaseRenewer:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

    async def _run_loop(self) -> None:
        """Основной цикл периодического продления владения."""
        while not self._stopped.is_set():
            try:
                # Ожидаем следующий интервал либо сигнал остановки
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=self.interval_seconds)
                    break
                except asyncio.TimeoutError:
                    pass

                # 1. Продлеваем lease в Core API
                if self.api_client and hasattr(self.api_client, "renew_command_lease_v2"):
                    status_code, data = await self.api_client.renew_command_lease_v2(
                        self.command_id,
                        self.worker_id,
                        self.claim_token,
                        lease_seconds=self.lease_seconds,
                    )
                    if status_code == 409:
                        logger.warning(
                            "Core API вернул 409 Conflict при продлении lease команды %s. Активируем отмену.",
                            self.command_id,
                        )
                        self.cancellation_token.set()
                        break
                    elif status_code != 200:
                        self._consecutive_failures += 1
                        logger.warning(
                            "Сбой продления lease команды %s (HTTP %d, подряд сбоев: %d)",
                            self.command_id,
                            status_code,
                            self._consecutive_failures,
                        )
                        if self._consecutive_failures >= 3:
                            logger.error(
                                "Критическое число сбоев связи с Core API для %s. Fail-closed отмена.",
                                self.command_id,
                            )
                            self.cancellation_token.set()
                            break
                    else:
                        self._consecutive_failures = 0

                # 2. Продлеваем distributed host lock в Redis
                if self.redis and self.host_lock_key and self.host_lock_token:
                    try:
                        res = await self.redis.eval(
                            EXTEND_LOCK_LUA,
                            1,
                            self.host_lock_key,
                            self.host_lock_token,
                            int(self.lease_seconds * 1000),
                        )
                        if res == 0:
                            logger.warning(
                                "Блокировка хоста %s утеряна или перехвачена в Redis. Активируем отмену.",
                                self.host_lock_key,
                            )
                            self.cancellation_token.set()
                            break
                    except Exception as redis_exc:
                        logger.warning("Ошибка продления блокировки хоста в Redis: %s", redis_exc)

            except asyncio.CancelledError:
                break
            except Exception as loop_exc:
                logger.warning("Ошибка в фоновом цикле LeaseRenewer: %s", loop_exc)
