import logging
import json
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.database.db import AsyncSessionLocal, User
from app.services.intraservice import get_tasks, get_task_lifetime, parse_api_date

logger = logging.getLogger(__name__)

redis_client = None
scheduler = AsyncIOScheduler()

def get_redis_client():
    global redis_client
    if redis_client is None:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return redis_client

async def close_redis():
    global redis_client
    if redis_client is not None:
        await redis_client.close()
        redis_client = None

async def process_user(
    user_id: int,
    redis_client: aioredis.Redis,
    base_web_url: str,
    semaphore: asyncio.Semaphore,
    current_time_utc: datetime,
    intraservice_tz: ZoneInfo
):
    """
    Обрабатывает обновления для одного пользователя под семафором и в рамках отдельной сессии БД.
    """
    async with semaphore:
        async with AsyncSessionLocal() as db:
            try:
                # Получаем свежий экземпляр пользователя, привязанный к текущей сессии
                db_user = await db.get(User, user_id)
                if not db_user:
                    return

                tg_id = db_user.tg_user_id
                is_user_id = db_user.is_user_id
                last_task_id = db_user.last_task_id or 0
                last_check_str = db_user.last_check_time
                auth_b64 = db_user.is_password_b64

                if not tg_id or not is_user_id or not auth_b64:
                    return

                # Время в БД сохраняется в UTC. Преобразуем его в локальное время системы IntraService.
                last_check_time_utc = parse_api_date(last_check_str)
                if last_check_time_utc:
                    if last_check_time_utc.tzinfo is None:
                        last_check_time_utc = last_check_time_utc.replace(tzinfo=timezone.utc)
                else:
                    db_user.last_check_time = current_time_utc.strftime("%Y-%m-%d %H:%M:%S")
                    await db.commit()
                    return

                last_check_time_local = last_check_time_utc.astimezone(intraservice_tz)
                api_filter_time = last_check_time_local.strftime("%Y-%m-%d %H:%M")

                pending_notifications = []

                # 1. Проверка НОВЫХ заявок
                tasks_data = await get_tasks(auth_b64, {"CreatedMoreThan": api_filter_time})
                if tasks_data is None:
                    logger.error(
                        "Не удалось получить новые заявки для пользователя %s из-за ошибки API. Пропуск итерации.",
                        tg_id
                    )
                    return

                new_tasks = []
                statuses_map = {}
                if isinstance(tasks_data, dict):
                    new_tasks = tasks_data.get("Tasks", [])
                    for s in tasks_data.get("Statuses", []):
                        statuses_map[s.get("Id")] = s.get("Name")
                elif isinstance(tasks_data, list):
                    new_tasks = tasks_data

                any_new_task = False
                for task in new_tasks:
                    if task["Id"] > last_task_id:
                        status_name = task.get("StatusName")
                        if not status_name and task.get("StatusId") in statuses_map:
                            status_name = statuses_map[task.get("StatusId")]

                        status_name = status_name or "N/A"

                        message_text = (
                            f"🆕 <b>Новая заявка #{task['Id']}</b>\n"
                            f"📝 Тема: {task['Name']}\n"
                            f"📊 Статус: {status_name}\n"
                            f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>Открыть в браузере</a>"
                        )

                        payload = {
                            "event_type": "new_task",
                            "tg_user_id": tg_id,
                            "task_id": task["Id"],
                            "task_name": task["Name"],
                            "message": message_text
                        }

                        pending_notifications.append(payload)
                        last_task_id = max(last_task_id, task["Id"])
                        any_new_task = True

                if any_new_task:
                    db_user.last_task_id = last_task_id

                # 2. Проверка КОММЕНТАРИЕВ и изменений статуса
                updated_tasks_data = await get_tasks(auth_b64, {
                    "ChangedMoreThan": api_filter_time,
                    "include": "executorids,status"
                })
                if updated_tasks_data is None:
                    logger.error(
                        "Не удалось получить обновленные заявки для пользователя %s из-за ошибки API. Пропуск итерации.",
                        tg_id
                    )
                    return

                updated_tasks = []
                updated_statuses_map = {}
                if isinstance(updated_tasks_data, dict):
                    updated_tasks = updated_tasks_data.get("Tasks", [])
                    for s in updated_tasks_data.get("Statuses", []):
                        updated_statuses_map[s.get("Id")] = s.get("Name")
                elif isinstance(updated_tasks_data, list):
                    updated_tasks = updated_tasks_data

                for task in updated_tasks:
                    executor_ids_str = str(task.get("ExecutorIds") or "")
                    executor_ids = [eid.strip() for eid in executor_ids_str.split(",") if eid.strip()]

                    if str(is_user_id) not in executor_ids:
                        continue

                    task_id = task["Id"]
                    lifetime_data = await get_task_lifetime(auth_b64, task_id)

                    events = []
                    if isinstance(lifetime_data, list):
                        events = lifetime_data
                    elif isinstance(lifetime_data, dict) and "TaskLifetimes" in lifetime_data:
                        events = lifetime_data["TaskLifetimes"]

                    if events:
                        for event in events:
                            if not isinstance(event, dict):
                                continue

                            raw_date = event.get("Date")
                            naive_event_date = parse_api_date(raw_date)

                            if naive_event_date:
                                event_date_local = naive_event_date.replace(tzinfo=intraservice_tz)

                                if event_date_local > last_check_time_local:
                                    if event.get("Comments"):
                                        message_text = (
                                            f"💬 <b>Новый комментарий в заявке #{task['Id']}</b> от <i>{event.get('Editor', 'Unknown')}</i>:\n"
                                            f"{event['Comments']}\n"
                                            f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>Открыть в браузере</a>"
                                        )

                                        payload = {
                                            "event_type": "new_comment",
                                            "tg_user_id": tg_id,
                                            "task_id": task["Id"],
                                            "task_name": task["Name"],
                                            "message": message_text
                                        }
                                        pending_notifications.append(payload)

                                    elif event.get("StatusId"):
                                        status_name = task.get("StatusName")
                                        if not status_name and task.get("StatusId") in updated_statuses_map:
                                            status_name = updated_statuses_map[task.get("StatusId")]

                                        status_name = status_name or "N/A"

                                        message_text = (
                                            f"🔄 <b>Статус заявки #{task['Id']} изменен</b> на: {status_name}\n"
                                            f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>Открыть в браузере</a>"
                                        )

                                        payload = {
                                            "event_type": "status_change",
                                            "tg_user_id": tg_id,
                                            "task_id": task["Id"],
                                            "task_name": task["Name"],
                                            "message": message_text
                                        }
                                        pending_notifications.append(payload)

                # Обновляем время последней проверки на текущее UTC
                db_user.last_check_time = current_time_utc.strftime("%Y-%m-%d %H:%M:%S")
                
                # Фиксируем состояние в БД
                await db.commit()

                # Только если коммит прошел успешно, отправляем сообщения в Redis
                for payload in pending_notifications:
                    try:
                        await redis_client.publish("intraservice_events", json.dumps(payload))
                    except Exception as pub_err:
                        logger.error(
                            "Не удалось опубликовать уведомление в Redis для пользователя %s, событие %s, заявка %s: %s. Payload: %s",
                            tg_id, payload.get("event_type"), payload.get("task_id"), pub_err, json.dumps(payload)
                        )

            except Exception as e:
                logger.error("Ошибка при обработке обновлений для пользователя %s: %s", user_id, e)
                await db.rollback()

async def check_updates():
    """
    Периодическая проверка новых заявок и комментариев на стороне Core API.
    Публикует события в Redis Pub/Sub.
    """
    base_web_url = settings.INTRASERVICE_URL.replace("/api/", "")
    redis = get_redis_client()
    
    # Семафор для ограничения одновременных запросов в IntraService
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)
    
    intraservice_tz = ZoneInfo(settings.INTRASERVICE_TZ)
    current_time_utc = datetime.now(timezone.utc)

    # Используем отдельную сессию для потокового чтения
    async with AsyncSessionLocal() as stream_db:
        try:
            # Читаем пользователей из базы батчами (yield_per), чтобы избежать утечек OOM
            query = select(User).execution_options(yield_per=100)
            result = await stream_db.stream(query)
            
            async for partition in result.partitions(100):
                tasks = []
                for row in partition:
                    user = row[0]
                    # Передаем только tg_user_id. Сессия будет открыта внутри process_user под семафором
                    tasks.append(
                        process_user(
                            user_id=user.tg_user_id,
                            redis_client=redis,
                            base_web_url=base_web_url,
                            semaphore=semaphore,
                            current_time_utc=current_time_utc,
                            intraservice_tz=intraservice_tz
                        )
                    )
                if tasks:
                    await asyncio.gather(*tasks)

        except Exception as e:
            logger.exception("Критическая ошибка в check_updates: %s", e)

async def start_worker():
    """
    Запускает планировщик APScheduler в фоновом режиме.
    """
    logger.info("Инициализация Redis клиента...")
    get_redis_client()
    
    logger.info("Запуск фонового воркера APScheduler...")
    polling_interval = settings.POLLING_INTERVAL
    
    scheduler.add_job(
        check_updates,
        "interval",
        seconds=polling_interval,
        id="intraservice_polling_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    scheduler.start()
    logger.info("Фоновый воркер запущен с интервалом %d секунд.", polling_interval)

async def stop_worker():
    """
    Останавливает планировщик и закрывает соединения.
    """
    logger.info("Остановка фонового воркера APScheduler...")
    try:
        scheduler.shutdown()
    except Exception as e:
        logger.error("Ошибка при остановке планировщика: %s", e)
    
    logger.info("Закрытие соединения с Redis...")
    await close_redis()
    logger.info("Фоновый воркер успешно остановлен.")
