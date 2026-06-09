import json
import logging
import asyncio
from typing import Optional
from worker_config import PRINTERS_KB_PATH
from orchestrator.schemas import KnowledgeBase
from orchestrator.orchestrator import PrinterOrchestrator
from worker_services.api_client import init_session, close_session
from worker_services.redis_listener import start_redis_listener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

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
            logger.info("База знаний принтеров успешно загружена: %d моделей зарегистрировано.", len(_kb.printers))
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

async def main():
    logger.info("Запуск микросервиса printer-worker...")
    
    # Инициализация сессии API-клиента
    await init_session()
    
    # Прогрев Базы Знаний
    get_kb()
    
    # Запуск фонового подписчика Redis
    listener_task = asyncio.create_task(start_redis_listener())
    
    # Event для ожидания сигнала остановки
    stop_event = asyncio.Event()
    
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        logger.info("Получен сигнал завершения работы...")
    finally:
        logger.info("Завершение работы фонового процесса...")
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
        
        # Гарантированное закрытие aiohttp сессии
        await close_session()
        logger.info("Микросервис printer-worker успешно остановлен.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Микросервис printer-worker остановлен пользователем.")
