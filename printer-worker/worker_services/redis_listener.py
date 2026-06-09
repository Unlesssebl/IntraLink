import logging
import json
import asyncio
import redis.asyncio as aioredis
from worker_config import REDIS_URL, MAX_CONCURRENT_JOBS
from orchestrator.schemas import PrintJob, JobState
from worker_services.api_client import get_task_details

logger = logging.getLogger(__name__)

# Семафор для ограничения одновременных сессий WinRM/SMB
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

async def _process_event(payload: dict) -> None:
    async with _semaphore:
        tg_user_id = payload.get("tg_user_id")
        task_id = payload.get("task_id")
        event_type = payload.get("event_type")

        logger.info("Обработка события '%s' для задачи #%d (пользователь %s)", event_type, task_id, tg_user_id)

        try:
            # Получаем подробности задачи из Core API
            task_data = await get_task_details(tg_user_id, task_id)
            if not task_data:
                logger.error("Не удалось загрузить подробности задачи #%d из Core API. Пропуск.", task_id)
                return

            # Текст заявки собирается из заголовка и описания
            raw_text = f"{task_data.get('Name', '')} {task_data.get('Description', '')}"

            # Извлекаем потенциально предзаполненные поля из кастомных полей IntraService
            # (Например, в поле CustomFields могут быть Имя ПК и Модель принтера)
            target_pc = None
            model_key = None
            
            custom_fields = task_data.get("CustomFields", [])
            for field in custom_fields:
                field_name = field.get("Name", "").lower()
                if "компьютер" in field_name or "pc" in field_name or "хост" in field_name:
                    target_pc = field.get("Value")
                elif "принтер" in field_name or "printer" in field_name or "модель" in field_name:
                    model_key = field.get("Value")

            # Создаем контекст выполнения PrintJob
            job = PrintJob(
                task_id=task_id,
                tg_user_id=tg_user_id,
                raw_text=raw_text,
                state=JobState.PENDING,
                target_pc=target_pc,
                model_key=model_key
            )

            # Создаем оркестратор и запускаем стейт-машину
            from main import get_orchestrator
            orchestrator = get_orchestrator()
            await orchestrator.run(job)

        except Exception as e:
            logger.exception("Ошибка при обработке события задачи #%d: %s", task_id, e)

async def start_redis_listener():
    """
    Фоновый процесс подписки на Redis Pub/Sub для прослушивания событий IntraService.
    """
    logger.info("Запуск фонового подписчика Redis Pub/Sub на канале 'intraservice_events'...")
    while True:
        redis = None
        try:
            redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe("intraservice_events")
                logger.info("Подписка на Redis Pub/Sub канал 'intraservice_events' успешно оформлена.")
                
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message is None or message.get("type") != "message":
                        continue
                    
                    payload_str = message["data"]
                    try:
                        payload = json.loads(payload_str)
                    except Exception as e:
                        logger.error("Ошибка парсинга JSON события из Redis: %s. Данные: %s", e, payload_str)
                        continue
                    
                    # Фильтруем события. Нас интересуют: new_task (новая заявка) и status_change
                    event_type = payload.get("event_type")
                    if event_type not in ("new_task", "status_change"):
                        continue
                    
                    # Проверяем, что в тексте сообщения или темы есть упоминание установки принтера
                    # Чтобы не реагировать на посторонние тикеты
                    msg_content = (payload.get("message") or "").lower()
                    task_name = (payload.get("task_name") or "").lower()
                    
                    is_printer_request = any(
                        word in msg_content or word in task_name
                        for word in ("принтер", "printer", "печать", "print", "установить принтер", "подключить принтер")
                    )
                    
                    if not is_printer_request:
                        logger.debug("Событие задачи #%d пропущено: не относится к установке принтера", payload.get("task_id"))
                        continue

                    # Запускаем обработку события асинхронно
                    asyncio.create_task(_process_event(payload))
                    
        except asyncio.CancelledError:
            logger.info("Слушатель Redis Pub/Sub остановлен.")
            break
        except Exception as e:
            logger.exception("Сбой соединения с Redis. Повторное подключение через 5 секунд... Ошибка: %s", e)
            await asyncio.sleep(5)
        finally:
            if redis is not None:
                await redis.close()
