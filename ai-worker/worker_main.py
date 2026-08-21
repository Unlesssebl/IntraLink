import json
import logging
import asyncio
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

        logger.info(
            "Запуск фонового перестроения RAG базы по квотам. Фильтр: %d, Услуг: %d",
            filter_id,
            len(service_ids),
        )

        try:
            await redis.set("rag_build:running", "true")
            await redis.delete("rag_build_logs_history")

            # Получаем и расшифровываем сервисный токен
            encrypted_auth = await redis.get("worker:service_auth_b64")
            if not encrypted_auth:
                await progress_callback(
                    redis,
                    "[ERROR] Учетные данные сервисного аккаунта отсутствуют в Redis.",
                )
                return

            auth_b64 = decrypt_token(encrypted_auth)

            await build_rag_dataset(
                filter_id=filter_id,
                global_quotas=global_quotas,
                service_quotas=service_quotas,
                service_ids=service_ids,
                auth_b64=auth_b64,
                progress_callback=lambda msg: progress_callback(redis, msg),
            )
        except Exception as e:
            logger.exception("Критический сбой перестроения RAG: %s", e)
            await progress_callback(redis, f"[ERROR] Критическая ошибка: {e}")
        finally:
            await redis.set("rag_build:running", "false")
            await progress_callback(
                redis, "[SYSTEM] Процесс перестроения базы RAG завершен."
            )


async def process_intraservice_event(
    redis: aioredis.Redis,
    payload: dict,
    classifier: AIClassifier,
    responder: AIResponder,
):
    event_type = payload.get("event_type")
    if event_type not in ("new_task", "executor_assigned"):
        return

    # При ручном назначении сервисной учетки (или другого бота) маршрутизируем как новую задачу
    if event_type == "executor_assigned":
        payload["event_type"] = "new_task"

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

        logger.info(
            "AI-Worker обрабатывает новую задачу #%s (Раздел: '%s')",
            task_id,
            task_data.get("ServiceName"),
        )

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
                    task_id,
                    task_data.get("ServiceName"),
                    classification.correct_service_name,
                    classification.reason,
                )
                comment_ok = await is_client.add_task_comment(
                    task_id, classification.comment_text
                )
                if comment_ok:
                    status_ok = await is_client.update_task_status(task_id, 30)
                    if status_ok:
                        logger.info(
                            "AI-классификатор: заявка #%d успешно отменена.", task_id
                        )
                        is_redirected = True
                        try:
                            await redis.hincrby("ai:stats", "redirected", 1)
                            await redis.hincrby("ai:stats", "total", 1)
                        except Exception:
                            pass

            if not is_redirected:
                # 1. Защита от повторной классификации (идемпотентность)
                await redis.set(classified_key, "1", ex=604800)
                
                # 2. Мгновенный реактивный триггер для printer-worker (и других)
                try:
                    await publish_event(redis, "ai_validated_events", payload)
                    logger.info("Заявка #%d прошла валидацию AI и отправлена в ai_validated_events", task_id)
                except Exception as e:
                    logger.error("Ошибка при публикации в ai_validated_events для заявки #%d: %s", task_id, e)

        if is_redirected:
            return

        # 3. AI-автоответчик для оставшихся задач
        await responder.process_new_task(task_data)

    except Exception as e:
        logger.exception(
            "Ошибка при обработке новой задачи #%d в AI-Worker: %s", task_id, e
        )
    finally:
        _active_tasks.discard(task_id)


async def handle_test_reply(
    redis: aioredis.Redis, payload: dict, responder: AIResponder
):
    task_id = payload.get("task_id")
    req_id = payload.get("req_id")
    if not task_id or not req_id:
        return

    logger.info(
        "Генерация тестового автоответа для задачи #%s, запрос %s", task_id, req_id
    )
    try:
        # Получаем данные задачи через is_client (Core API)
        task_data = await is_client.get_single_task(task_id)
        if not task_data:
            result = {
                "status": "error",
                "message": f"Задача #{task_id} не найдена в IntraService.",
            }
        else:
            # Извлекаем саму задачу из ключа "Task"
            task_details = task_data.get("Task") if isinstance(task_data, dict) else None
            if not task_details:
                task_details = task_data

            reply_result = await responder.generate_reply(task_details)
            result = {
                "status": "success",
                "generated_reply": reply_result.reply_text,
                "confidence": reply_result.confidence,
                "can_resolve": reply_result.can_resolve,
                "needs_clarification": reply_result.needs_clarification,
                "reason": reply_result.reason,
            }
    except Exception as e:
        logger.exception("Ошибка генерации тестового ответа: %s", e)
        result = {"status": "error", "message": str(e)}

    # Публикуем результат в Redis Pub/Sub канал
    await redis.publish(f"ai:test_reply_chan:{req_id}", json.dumps(result))


async def publish_event(
    redis: aioredis.Redis,
    channel_or_stream: str,
    payload: dict,
    maxlen: int = 10000,
) -> None:
    """
    Dual-Publishing: запись в Redis Stream (at-least-once) и параллельно в Pub/Sub.
    """
    try:
        import orjson
        payload_json = orjson.dumps(payload, default=str).decode("utf-8")
    except ImportError:
        payload_json = json.dumps(payload, default=str)

    stream_name = channel_or_stream if channel_or_stream.startswith("stream:") else f"stream:{channel_or_stream}"
    pubsub_channel = channel_or_stream.replace("stream:", "")

    try:
        await redis.xadd(stream_name, {"payload": payload_json}, maxlen=maxlen, approximate=True)
    except Exception as e:
        logger.error("Ошибка при записи в Redis Stream %s: %s", stream_name, e)

    try:
        await redis.publish(pubsub_channel, payload_json)
    except Exception as e:
        logger.error("Ошибка при публикации в Pub/Sub канал %s: %s", pubsub_channel, e)


async def _listen_pubsub_actions(redis: aioredis.Redis, responder: AIResponder):
    """Слушает управляющие команды из канала ai_actions."""
    try:
        async with redis.pubsub() as pubsub:
            await pubsub.subscribe("ai_actions")
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None or message.get("type") != "message":
                    await asyncio.sleep(0.1)
                    continue

                payload_str = message.get("data")
                if not isinstance(payload_str, (str, bytes)):
                    continue

                try:
                    try:
                        import orjson
                        payload = orjson.loads(payload_str)
                    except ImportError:
                        payload = json.loads(payload_str)

                    event_type = payload.get("event_type")
                    if event_type == "rag_build":
                        asyncio.create_task(handle_rag_build(redis, payload))
                    elif event_type == "test_reply":
                        asyncio.create_task(handle_test_reply(redis, payload, responder))
                except Exception as e:
                    logger.error("Ошибка парсинга управляющего действия ai_actions: %s", e)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("Сбой слушателя ai_actions: %s", e)


async def start_redis_listener():
    logger.info("Инициализация AI модулей...")
    classifier = AIClassifier()
    responder = AIResponder()

    STREAM_NAME = "stream:intraservice_events"
    GROUP_NAME = "ai_worker_group"
    CONSUMER_NAME = "ai_worker_1"

    logger.info("Запуск слушателя Redis Streams (%s / %s)...", STREAM_NAME, GROUP_NAME)
    while True:
        redis = None
        action_task = None
        try:
            redis = get_redis_client()

            # Создаем группу потребителей (если еще не создана)
            try:
                await redis.xgroup_create(STREAM_NAME, GROUP_NAME, id="0", mkstream=True)
                logger.info("Создана Consumer Group '%s' для стрима '%s'", GROUP_NAME, STREAM_NAME)
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    logger.debug("Инициализация Consumer Group: %s", e)

            # Запускаем фоновый слушатель управляющих Pub/Sub команд
            action_task = asyncio.create_task(_listen_pubsub_actions(redis, responder))

            while True:
                # Читаем новые сообщения из стрима
                try:
                    entries = await redis.xreadgroup(
                        groupname=GROUP_NAME,
                        consumername=CONSUMER_NAME,
                        streams={STREAM_NAME: ">"},
                        count=10,
                        block=2000,
                    )
                except Exception as read_err:
                    logger.warning("Ошибка чтения из Redis Stream: %s", read_err)
                    await asyncio.sleep(2)
                    continue

                if not entries:
                    continue

                for stream, messages in entries:
                    for msg_id, data in messages:
                        payload_str = data.get("payload") if isinstance(data, dict) else None
                        if not payload_str:
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)
                            continue

                        try:
                            try:
                                import orjson
                                payload = orjson.loads(payload_str)
                            except ImportError:
                                payload = json.loads(payload_str)

                            # Запускаем обработку события
                            asyncio.create_task(
                                process_intraservice_event(
                                    redis, payload, classifier, responder
                                )
                            )
                            # Подтверждаем получение
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)
                        except Exception as e:
                            logger.error(
                                "Ошибка обработки сообщения %s из стрима: %s", msg_id, e
                            )
                            await redis.xack(STREAM_NAME, GROUP_NAME, msg_id)

        except asyncio.CancelledError:
            logger.info("Слушатель Redis Streams остановлен.")
            if action_task:
                action_task.cancel()
            break
        except Exception as e:
            logger.exception(
                "Сбой соединения с Redis. Переподключение через 5 секунд... Ошибка: %s",
                e,
            )
            if action_task:
                action_task.cancel()
            await asyncio.sleep(5)


async def main():
    logger.info("Запуск микросервиса ai-worker...")

    # Инициализация сессии API-клиента
    await is_client.init_session()

    # Сброс возможных зависших флагов после перезапуска контейнера
    try:
        r = get_redis_client()
        await r.set("rag_build:running", "false")
    except Exception as e:
        logger.error("Не удалось сбросить флаг rag_build:running при запуске: %s", e)

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
