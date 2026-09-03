"""
Автономный сервис периодического опроса IntraService (Poller Service).
Работает независимо от HTTP API Gateway с защитой от Split-Brain через Redis Leader Lock.
"""
import asyncio
import logging
import os
import signal
import socket
import sys
import uuid
from typing import Optional

from app.config import settings
from app.services.intraservice import close_session, init_session
from app.services.worker import (
    check_updates,
    close_redis,
    get_redis_client,
    sync_service_catalog,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [POLLER] - %(levelname)s - %(message)s",
)
logger = logging.getLogger("intralink.poller")

LEADER_LOCK_KEY = "lock:poller_leader"
LEADER_LOCK_TTL = 15  # Секунд действия замка лидера


class IntraServicePoller:
    """
    Демон периодического опроса IntraService с механизмом распределенного лидерства.
    """

    def __init__(self):
        hostname = socket.gethostname()
        random_suffix = uuid.uuid4().hex[:6]
        self.worker_id = f"poller:{hostname}:{os.getpid()}:{random_suffix}"
        self.is_running = False
        self._is_leader = False

    async def _try_acquire_leader_lock(self, redis) -> bool:
        """
        Пытается захватить или продлить статус лидера опроса.
        """
        try:
            # 1. Попытка первичного захвата (SET NX EX)
            acquired = await redis.set(
                LEADER_LOCK_KEY, self.worker_id, nx=True, ex=LEADER_LOCK_TTL
            )
            if acquired:
                if not self._is_leader:
                    logger.info("👑 Захвачен статус Лидера опроса (%s)", self.worker_id)
                self._is_leader = True
                return True

            # 2. Если замок уже наш — продлеваем TTL (Heartbeat)
            current_owner = await redis.get(LEADER_LOCK_KEY)
            if current_owner == self.worker_id:
                await redis.expire(LEADER_LOCK_KEY, LEADER_LOCK_TTL)
                self._is_leader = True
                return True

            if self._is_leader:
                logger.warning(
                    "⚠️ Потерян статус Лидера опроса! Текущий владелец: %s",
                    current_owner,
                )
            self._is_leader = False
            return False
        except Exception as e:
            logger.error("Ошибка при проверке Leader Lock в Redis: %s", e)
            self._is_leader = False
            return False

    async def _release_leader_lock(self, redis) -> None:
        """Освобождает замок лидера при штатной остановке."""
        try:
            current_owner = await redis.get(LEADER_LOCK_KEY)
            if current_owner == self.worker_id:
                await redis.delete(LEADER_LOCK_KEY)
                logger.info("Замок Лидера опроса успешно освобожден.")
        except Exception as e:
            logger.debug("Ошибка освобождения Leader Lock: %s", e)

    async def run(self) -> None:
        """Главный цикл демона опроса."""
        logger.info(
            "🚀 Запуск IntraService Poller Daemon (%s)... Интервал: %s сек",
            self.worker_id,
            settings.POLLING_INTERVAL,
        )
        self.is_running = True

        # Инициализация внешних сессий
        await init_session()
        redis = get_redis_client()

        # Первичная фоновая синхронизация справочника каталога
        try:
            await sync_service_catalog()
        except Exception as e:
            logger.warning("Ошибка первичной синхронизации каталога: %s", e)

        while self.is_running:
            try:
                # 1. Проверяем статус лидера
                is_leader = await self._try_acquire_leader_lock(redis)

                if is_leader:
                    # 2. Выполняем опрос заявок
                    await check_updates()
                else:
                    logger.debug(
                        "Реплика в режиме ожидания (Standby). Лидер активен."
                    )

            except asyncio.CancelledError:
                logger.info("Получен сигнал завершения цикла опроса.")
                break
            except Exception as e:
                logger.exception("Непредвиденная ошибка в цикле опроса: %s", e)

            # Ожидание следующей итерации
            try:
                await asyncio.sleep(settings.POLLING_INTERVAL)
            except asyncio.CancelledError:
                break

        # Завершение работы
        await self._release_leader_lock(redis)
        await close_session()
        await close_redis()
        logger.info("IntraService Poller Daemon остановлен.")

    def stop(self) -> None:
        """Останавливает цикл опроса."""
        self.is_running = False


async def main():
    poller = IntraServicePoller()

    def handle_signal(sig, frame):
        logger.info("Получен сигнал завершения процесса (%s)...", sig)
        poller.stop()

    # Регистрация обработчиков сигналов
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, poller.stop)

    try:
        await poller.run()
    except (KeyboardInterrupt, SystemExit):
        poller.stop()


if __name__ == "__main__":
    asyncio.run(main())
