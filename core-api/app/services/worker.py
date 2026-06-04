import logging
import json
from datetime import datetime
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

async def check_updates():
    """
    Периодическая проверка новых заявок и комментариев на стороне Core API.
    Публикует события в Redis Pub/Sub.
    """
    base_web_url = settings.INTRASERVICE_URL.replace("/api/", "")
    redis = get_redis_client()
    
    async with AsyncSessionLocal() as db:
        try:
            query = select(User)
            result = await db.execute(query)
            users = result.scalars().all()
            if not users:
                return

            for user in users:
                tg_id = user.tg_user_id
                try:
                    is_user_id = user.is_user_id
                    last_task_id = user.last_task_id or 0
                    last_check_str = user.last_check_time
                    auth_b64 = user.is_password_b64

                    if not tg_id or not is_user_id or not auth_b64:
                        continue

                    current_time = datetime.now()
                    last_check_time = parse_api_date(last_check_str) if last_check_str else None

                    if not last_check_time:
                        user.last_check_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
                        await db.commit()
                        continue

                    api_filter_time = last_check_time.strftime("%Y-%m-%d %H:%M")

                    # 1. Проверка НОВЫХ заявок
                    tasks_data = await get_tasks(auth_b64, {"CreatedMoreThan": api_filter_time})

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

                            await redis.publish("intraservice_events", json.dumps(payload))
                            last_task_id = max(last_task_id, task["Id"])
                            any_new_task = True

                    if any_new_task:
                        user.last_task_id = last_task_id
                        await db.commit()

                    # 2. Проверка КОММЕНТАРИЕВ и изменений статуса
                    updated_tasks_data = await get_tasks(auth_b64, {
                        "ChangedMoreThan": api_filter_time,
                        "include": "executorids,status"
                    })

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

                        lifetime_data = await get_task_lifetime(auth_b64, task["Id"])

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
                                event_date = parse_api_date(raw_date)

                                if event_date and event_date > last_check_time:
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
                                        await redis.publish("intraservice_events", json.dumps(payload))

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
                                        await redis.publish("intraservice_events", json.dumps(payload))

                    # В конце обновляем last_check_time
                    user.last_check_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
                    await db.commit()

                except Exception as e:
                    logger.error("Ошибка при обработке обновлений для пользователя %s: %s", tg_id, e)
                    await db.rollback()

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
        replace_existing=True
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
