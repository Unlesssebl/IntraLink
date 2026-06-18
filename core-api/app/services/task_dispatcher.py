import logging
from app.config import settings

logger = logging.getLogger(__name__)

class TaskDispatcher:
    def __init__(self):
        # В будущем здесь может быть расширен реестр исполнителей
        pass

    async def dispatch(self, task: dict, redis_client) -> bool:
        """
        Маршрутизирует задачу на специализированные воркеры.
        Возвращает True, если задача смаршрутизирована на исполнителя.
        """
        task_id = task.get("Id")
        service_id = task.get("ServiceId")

        if not task_id:
            return False

        # 1. Проверяем, относится ли задача к принтерам
        if service_id and service_id in settings.PRINTER_SERVICE_IDS:
            # Проверяем heartbeat воркера принтеров
            worker_status = await redis_client.get("printer_worker:status")
            if worker_status == "online":
                # Устанавливаем guard-флаг, чтобы AI Responder не трогал эту задачу
                await redis_client.set(f"dispatched:{task_id}", "printer", ex=86400)
                logger.info(
                    "Задача #%s смаршрутизирована на printer-worker. Установлен guard-ключ dispatched",
                    task_id
                )
                
                # При необходимости мы можем дополнительно опубликовать команду в printer_actions,
                # но так как printer-worker сейчас слушает intraservice_events самостоятельно,
                # достаточно установить guard-ключ в Redis, чтобы AI Responder пропустил задачу.
                return True
            else:
                logger.warning(
                    "Задача #%s относится к принтерам, но printer-worker оффлайн (статус: %s). Диспетчеризация пропущена.",
                    task_id, worker_status
                )
                
        # Сюда можно добавить маршрутизацию для новых воркеров (soft-worker и др.)

        return False
