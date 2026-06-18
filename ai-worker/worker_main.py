import json
import logging
import asyncio
from typing import Optional
import redis.asyncio as aioredis

from core.config import settings
from core.crypto import decrypt_token
from services.classifier import AIClassifier
from services.responder import AIResponder
from services.rag_builder import build_rag_dataset
from services import is_client
from services.redis_client import get_redis_client, close_redis

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

_active_tasks = set()
_rag_semaphore = asyncio.Semaphore(1)


async def progress_callback(redis: aioredis.Redis, msg: str):
    try:
        await redis.publish("rag_build_logs", msg)
        await redis.rpush("rag_build_logs_history", msg)
        await redis.expire("rag_build_logs_history", 86400)
    except Exception as e:
        logger.error("Ошибка при публикации логов RAG в Redis: %s", e)


async def handle_rag_build(redis: aioredis.Redis, payload: dict):
    """
    Фоновый запуск перестроения базы знаний RAG с квотами и фильтром.
    """
    async with _rag_semaphore:
        filter_id = payload.get("filter_id", 0)
        global_quotas = payload.get("global_quotas", {"28": 10, "30": 5})
        service_quotas = payload.get("service_quotas", {})
        service_ids = payload.get("service_ids", [])
        
        logger.info("Запуск фонового перестроения RAG базы по квотам. Фильтр: %d, Услуг: %d", filter_id, len(service_ids))
        
        try:
            await redis.set("rag_build:running", "true")
            await redis.delete("rag_build_logs_history")
            
            # Получаем и расшифровываем сервисный токен
            encrypted_auth = await redis.get("worker:service_auth_b64")
            if not encrypted_auth:
                await progress_callback(redis, "[ERROR] Учетные данные сервисного аккаунта отсутствуют в Redis.")
                return
                
            auth_b64 = decrypt_token(encrypted_auth)
            
            await build_rag_dataset(
                filter_id=filter_id,
                global_quotas=global_quotas,
                service_quotas=service_quotas,
                service_ids=service_ids,
                auth_b64=auth_b64,
                progress_callback=lambda msg: progress_callback(redis, msg)
            )
        except Exception as e:
            logger.exception("Критический сбой перестроения RAG: %s", e)
            await progress_callback(redis, f"[ERROR] Критическая ошибка: {e}")
        finally:
            await redis.set("rag_build:running", "false")
            await progress_callback(redis, "[SYSTEM] Процесс перестроения базы RAG завершен.")


async def process_intraservice_event(redis: aioredis.Redis, payload: dict, classifier: AIClassifier, responder: AIResponder):
    event_type = payload.get("event_type")
    if event_type != "new_task":
        return

    task_data = payload.get("task_data")
    if not task_data:
        return

    task_id = task_data.get("Id")
    if not task_id:
        return

    if task_id in _active_tasks:
        return

    _active_tasks.add(task_id)
    try:
        # 1. Ранняя фильтрация не-IT разделов
        service_name = (task_data.get("ServiceName") or "").lower()
        exclude_keywords = ["ахо", "хозяйствен", "канцеляри", "клининг", "охрана"]
        if any(kw in service_name for kw in exclude_keywords):
            return

        # Динамически загружаем ID разделов для автоответов из Redis (с fallback на settings)
        auto_reply_services_str = await redis.get("config:auto_reply_service_ids")
        if auto_reply_services_str:
            if isinstance(auto_reply_services_str, bytes):
                auto_reply_services_str = auto_reply_services_str.decode("utf-8")
            settings.AUTO_REPLY_SERVICE_IDS = json.loads(auto_reply_services_str)
        
        auto_reply_mode_val = await redis.get("config:auto_reply_mode")
        if auto_reply_mode_val is not None:
            if isinstance(auto_reply_mode_val, bytes):
                settings.AUTO_REPLY_MODE = auto_reply_mode_val.decode("utf-8")
            else:
                settings.AUTO_REPLY_MODE = str(auto_reply_mode_val)

        logger.info("AI-Worker обрабатывает новую задачу #%s (Раздел: '%s')", task_id, task_data.get("ServiceName"))

        # 2. Проверка и запуск классификатора
        classified_key = f"ai_classified:{task_id}"
        is_redirected = False
        
        if not await redis.get(classified_key):
            classification = await classifier.classify_task(task_data)
            
            try:
                await redis.hincrby("ai:stats", "classifications", 1)
            except Exception:
                pass

            if classification.action == "redirect":
                logger.info(
                    "AI-классификатор: перенаправление заявки #%d из '%s' в '%s'. Причина: %s",
                    task_id, task_data.get("ServiceName"), classification.correct_service_name, classification.reason
                )
                comment_ok = await is_client.add_task_comment(task_id, classification.comment_text)
                if comment_ok:
                    status_ok = await is_client.update_task_status(task_id, 30)
                    if status_ok:
                        logger.info("AI-классификатор: заявка #%d успешно отменена.", task_id)
                        is_redirected = True
                        try:
                            await redis.hincrby("ai:stats", "redirected", 1)
                            await redis.hincrby("ai:stats", "total", 1)
                        except Exception:
                            pass

            await redis.set(classified_key, "1", ex=604800)

        if is_redirected:
            return

        # 3. AI-автоответчик для оставшихся задач
        await responder.process_new_task(task_data)

    except Exception as e:
        logger.exception("Ошибка при обработке новой задачи #%d в AI-Worker: %s", task_id, e)
    finally:
        _active_tasks.discard(task_id)


async def handle_test_reply(redis: aioredis.Redis, payload: dict, responder: AIResponder):
    task_id = payload.get("task_id")
    req_id = payload.get("req_id")
    if not task_id or not req_id:
        return
        
    logger.info("Генерация тестового автоответа для задачи #%s, запрос %s", task_id, req_id)
    try:
        # Получаем данные задачи через is_client (Core API)
        task_data = await is_client.get_single_task(task_id)
        if not task_data:
            result = {"status": "error", "message": f"Задача #{task_id} не найдена в IntraService."}
        else:
            reply_result = await responder.generate_reply(task_data)
            result = {
                "status": "success",
                "generated_reply": reply_result.reply_text,
                "confidence": reply_result.confidence,
                "can_resolve": reply_result.can_resolve,
                "needs_clarification": reply_result.needs_clarification,
                "reason": reply_result.reason
            }
    except Exception as e:
        logger.exception("Ошибка генерации тестового ответа: %s", e)
        result = {"status": "error", "message": str(e)}
        
    # Записываем результат в Redis с TTL 60 сек
    await redis.set(f"ai:test_reply:{req_id}", json.dumps(result), ex=60)


async def start_redis_listener():
    logger.info("Инициализация AI модулей...")
    classifier = AIClassifier()
    responder = AIResponder()

    logger.info("Запуск слушателя Redis Pub/Sub...")
    while True:
        redis = None
        try:
            redis = get_redis_client()
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe("intraservice_events", "ai_actions")
                logger.info("Успешно подписались на каналы 'intraservice_events' и 'ai_actions'")

                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message is None or message.get("type") != "message":
                        continue

                    payload_str = message.get("data")
                    if not isinstance(payload_str, (str, bytes)):
                        continue

                    try:
                        payload = json.loads(payload_str)
                    except Exception as e:
                        logger.error("Ошибка парсинга JSON события: %s. Данные: %s", e, payload_str)
                        continue

                    channel = message.get("channel")
                    
                    if channel == "ai_actions":
                        event_type = payload.get("event_type")
                        if event_type == "rag_build":
                            asyncio.create_task(handle_rag_build(redis, payload))
                        elif event_type == "test_reply":
                            asyncio.create_task(handle_test_reply(redis, payload, responder))
                    elif channel == "intraservice_events":
                        asyncio.create_task(process_intraservice_event(redis, payload, classifier, responder))

        except asyncio.CancelledError:
            logger.info("Слушатель Redis Pub/Sub остановлен.")
            break
        except Exception as e:
            logger.exception("Сбой соединения с Redis. Переподключение через 5 секунд... Ошибка: %s", e)
            await asyncio.sleep(5)


async def main():
    logger.info("Запуск микросервиса ai-worker...")
    
    # Инициализация сессии API-клиента
    await is_client.init_session()

    # Запуск фонового подписчика Redis
    listener_task = asyncio.create_task(start_redis_listener())

    # Ожидание остановки
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        logger.info("Получен сигнал завершения работы...")
    finally:
        listener_task.cancel()
        try:
            await asyncio.gather(listener_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass

        # Гарантированное закрытие aiohttp сессии и Redis
        await is_client.close_session()
        await close_redis()
        logger.info("Микросервис ai-worker успешно остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Микросервис ai-worker остановлен пользователем.")
