import json
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from worker_config import PRINTERS_KB_PATH
from orchestrator.schemas import KnowledgeBase
from orchestrator.orchestrator import PrinterOrchestrator, current_task_id
from worker_services.api_client import init_session, close_session
from worker_services.redis_listener import start_redis_listener

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Пользовательский хэндлер для трансляции логов конкретной задачи в Redis
# Пользовательский хэндлер для трансляции логов конкретной задачи в Redis
class RedisLogHandler(logging.Handler):
    def __init__(self, loop=None):
        super().__init__()
        try:
            self.loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None

    def emit(self, record):
        task_id = current_task_id.get()
        if task_id and self.loop:
            try:
                log_message = self.format(record)

                async def pub():
                    try:
                        from worker_services.redis_listener import get_redis

                        r = get_redis()
                        await r.publish(f"printer_job_logs:{task_id}", log_message)
                        history_key = f"printer_job_logs_history:{task_id}"
                        await r.rpush(history_key, log_message)
                        await r.expire(history_key, 86400)
                    except Exception:
                        pass

                try:
                    current_loop = asyncio.get_running_loop()
                except RuntimeError:
                    current_loop = None

                if current_loop is self.loop:
                    self.loop.create_task(pub())
                else:
                    asyncio.run_coroutine_threadsafe(pub(), self.loop)
            except Exception:
                pass


_kb: Optional[KnowledgeBase] = None
_orchestrator: Optional[PrinterOrchestrator] = None


def get_kb() -> KnowledgeBase:
    """
    Лениво инициализирует и возвращает валидированную Базу Знаний принтеров.
    """
    global _kb
    if _kb is None:
        try:
            with open(PRINTERS_KB_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            _kb = KnowledgeBase.model_validate(data)
            logger.info(
                "База знаний принтеров успешно загружена: %d моделей зарегистрировано.",
                len(_kb.printers),
            )
        except Exception as e:
            logger.error("Критическая ошибка загрузки Базы Знаний принтеров: %s", e)
            raise e
    return _kb


def get_orchestrator() -> PrinterOrchestrator:
    """
    Возвращает экземпляр Оркестратора.
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = PrinterOrchestrator(get_kb())
    return _orchestrator


async def start_heartbeat():
    """
    Фоновая задача периодической отправки статуса "online" в Redis.
    """
    from worker_services.redis_listener import get_redis

    logger.info("Запуск фоновой отправки heartbeat в Redis...")
    while True:
        try:
            r = get_redis()
            await r.setex("printer_worker:status", 10, "online")
        except Exception as e:
            logger.error("Ошибка при отправке heartbeat в Redis: %s", e)
        await asyncio.sleep(5)


async def main():
    logger.info("Запуск микросервиса printer-worker...")

    # Установка глобального ThreadPoolExecutor для изоляции тяжелых I/O операций (SMB, WinRM)
    io_pool = ThreadPoolExecutor(max_workers=100, thread_name_prefix="PrinterIO")
    loop = asyncio.get_running_loop()
    loop.set_default_executor(io_pool)

    # Регистрация RedisLogHandler на корневом логгере
    root_logger = logging.getLogger()
    redis_handler = RedisLogHandler()
    redis_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    )
    root_logger.addHandler(redis_handler)

    # Инициализация сессии API-клиента
    await init_session()

    # Прогрев Базы Знаний
    get_kb()

    # Запуск фонового подписчика Redis
    listener_task = asyncio.create_task(start_redis_listener())

    # Запуск фонового heartbeat
    heartbeat_task = asyncio.create_task(start_heartbeat())

    # Event для ожидания сигнала остановки
    stop_event = asyncio.Event()

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        logger.info("Получен сигнал завершения работы...")
    finally:
        logger.info("Завершение работы фонового процесса...")
        listener_task.cancel()
        heartbeat_task.cancel()
        try:
            await asyncio.gather(listener_task, heartbeat_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass

        # Гарантированное закрытие aiohttp сессии
        await close_session()
        # Завершение пула потоков
        io_pool.shutdown(wait=False)
        logger.info("Микросервис printer-worker успешно остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Микросервис printer-worker остановлен пользователем.")
