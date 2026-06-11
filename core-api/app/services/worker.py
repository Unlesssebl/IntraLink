import asyncio
import json
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.database.db import AsyncSessionLocal, User
from app.services.intraservice import get_task_lifetime, get_tasks, parse_api_date

logger = logging.getLogger(__name__)

redis_client = None
scheduler = AsyncIOScheduler()

def get_redis_client():
    global redis_client  # noqa: PLW0603
    if redis_client is None:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return redis_client

async def close_redis():
    global redis_client  # noqa: PLW0603
    if redis_client is not None:
        await redis_client.close()
        redis_client = None

async def _get_api_filter_time(
    db_user: User,
    db,
    current_time_utc: datetime,
    intraservice_tz: ZoneInfo,
) -> tuple[datetime, str] | None:
    """
    Возвращает (last_check_time_local, api_filter_time) или None,
    если требуется обновить last_check_time в БД и завершить обработку.
    """
    last_check_str = db_user.last_check_time
    last_check_time_utc = parse_api_date(last_check_str)
    if last_check_time_utc:
        if last_check_time_utc.tzinfo is None:
            last_check_time_utc = last_check_time_utc.replace(tzinfo=UTC)
    else:
        db_user.last_check_time = current_time_utc.strftime("%Y-%m-%d %H:%M:%S")
        await db.commit()
        return None

    last_check_time_local = last_check_time_utc.astimezone(intraservice_tz)
    api_filter_time = last_check_time_local.strftime("%Y-%m-%d %H:%M")
    return last_check_time_local, api_filter_time

async def _check_new_tasks(
    db_user: User,
    auth_b64: str,
    api_filter_time: str,
    base_web_url: str,
) -> list[dict] | None:
    """
    Проверяет новые задачи и возвращает список событий для Redis
    или None в случае ошибки API.
    """
    tasks_data = await get_tasks(
        auth_b64,
        {"CreatedMoreThan": api_filter_time, "include": "executorids"},
    )
    if tasks_data is None:
        logger.error(
            "Не удалось получить новые заявки для пользователя %s "
            "из-за ошибки API. Пропуск итерации.",
            db_user.tg_user_id,
        )
        return None

    new_tasks = []
    statuses_map = {}
    if isinstance(tasks_data, dict):
        new_tasks = tasks_data.get("Tasks", [])
        for s in tasks_data.get("Statuses", []):
            statuses_map[s.get("Id")] = s.get("Name")
    elif isinstance(tasks_data, list):
        new_tasks = tasks_data

    notifications = []
    last_task_id = db_user.last_task_id or 0
    any_new_task = False

    for task in new_tasks:
        # Уведомляем только если пользователь является исполнителем
        executor_ids_str = str(task.get("ExecutorIds") or "")
        executor_ids = [
            eid.strip()
            for eid in executor_ids_str.split(",")
            if eid.strip()
        ]
        if str(db_user.is_user_id) not in executor_ids:
            continue

        if task["Id"] > last_task_id:
            status_name = task.get("StatusName")
            if not status_name and task.get("StatusId") in statuses_map:
                status_name = statuses_map[task.get("StatusId")]

            status_name = status_name or "N/A"

            message_text = (
                f"🆕 <b>Новая заявка #{task['Id']}</b>\n"
                f"📝 Тема: {task['Name']}\n"
                f"📊 Статус: {status_name}\n"
                f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>"
                "Открыть в браузере</a>"
            )

            payload = {
                "event_type": "new_task",
                "tg_user_id": db_user.tg_user_id,
                "is_user_id": db_user.is_user_id,
                "is_login": db_user.is_login,
                "task_id": task["Id"],
                "task_name": task["Name"],
                "text": message_text,
            }

            notifications.append(payload)
            last_task_id = max(last_task_id, task["Id"])
            any_new_task = True

    if any_new_task:
        db_user.last_task_id = last_task_id

    return notifications

def _process_lifetime_event(  # noqa: PLR0913
    event: dict,
    task: dict,
    db_user: User,
    base_web_url: str,
    last_check_time_local: datetime,
    intraservice_tz: ZoneInfo,
    updated_statuses_map: dict,
) -> dict | None:
    """
    Обрабатывает одно событие из истории изменений задачи.
    Возвращает payload уведомления или None.
    """
    if not isinstance(event, dict):
        return None

    raw_date = event.get("Date")
    naive_event_date = parse_api_date(raw_date)
    if not naive_event_date:
        return None

    event_date_local = naive_event_date.replace(tzinfo=intraservice_tz)
    if event_date_local <= last_check_time_local:
        return None

    if event.get("Comments"):
        editor = event.get("Editor", "Unknown")
        message_text = (
            f"💬 <b>Новый комментарий в заявке #{task['Id']}</b> "
            f"от <i>{editor}</i>:\n"
            f"{event['Comments']}\n"
            f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>"
            "Открыть в браузере</a>"
        )
        return {
            "event_type": "new_comment",
            "tg_user_id": db_user.tg_user_id,
            "is_user_id": db_user.is_user_id,
            "is_login": db_user.is_login,
            "task_id": task["Id"],
            "task_name": task["Name"],
            "text": message_text,
        }

    if event.get("StatusId"):
        status_name = task.get("StatusName")
        if not status_name and task.get("StatusId") in updated_statuses_map:
            status_name = updated_statuses_map[task.get("StatusId")]

        status_name = status_name or "N/A"
        message_text = (
            f"🔄 <b>Статус заявки #{task['Id']} изменен</b> "
            f"на: {status_name}\n"
            f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>"
            "Открыть в браузере</a>"
        )
        return {
            "event_type": "status_change",
            "tg_user_id": db_user.tg_user_id,
            "is_user_id": db_user.is_user_id,
            "is_login": db_user.is_login,
            "task_id": task["Id"],
            "task_name": task["Name"],
            "text": message_text,
            "status_id": int(event.get("StatusId")),
        }

    if "Executors" in event:
        editor = event.get("Editor", "Unknown")
        message_text = (
            f"👤 <b>Назначен исполнитель в заявке #{task['Id']}</b> "
            f"редактором <i>{editor}</i>:\n"
            f"Новый исполнитель: {event['Executors']}\n"
            f"🔗 <a href='{base_web_url}/Task/View/{task['Id']}'>"
            "Открыть в браузере</a>"
        )
        return {
            "event_type": "executor_assigned",
            "tg_user_id": db_user.tg_user_id,
            "is_user_id": db_user.is_user_id,
            "is_login": db_user.is_login,
            "task_id": task["Id"],
            "task_name": task["Name"],
            "text": message_text,
            "status_id": int(task.get("StatusId")) if task.get("StatusId") is not None else None,
        }

    return None

async def _check_task_updates(
    db_user: User,
    api_filter_time: str,
    base_web_url: str,
    last_check_time_local: datetime,
    intraservice_tz: ZoneInfo,
) -> list[dict] | None:
    """
    Проверяет обновления задач и возвращает список событий для Redis или None.
    """
    updated_tasks_data = await get_tasks(
        db_user.is_password_b64,
        {
            "ChangedMoreThan": api_filter_time,
            "include": "executorids,status",
        },
    )
    if updated_tasks_data is None:
        logger.error(
            "Не удалось получить обновленные заявки для пользователя %s "
            "из-за ошибки API. Пропуск итерации.",
            db_user.tg_user_id,
        )
        return None

    updated_tasks = []
    updated_statuses_map = {}
    if isinstance(updated_tasks_data, dict):
        updated_tasks = updated_tasks_data.get("Tasks", [])
        for s in updated_tasks_data.get("Statuses", []):
            updated_statuses_map[s.get("Id")] = s.get("Name")
    elif isinstance(updated_tasks_data, list):
        updated_tasks = updated_tasks_data

    notifications = []

    for task in updated_tasks:
        executor_ids_str = str(task.get("ExecutorIds") or "")
        executor_ids = [
            eid.strip()
            for eid in executor_ids_str.split(",")
            if eid.strip()
        ]
        if str(db_user.is_user_id) not in executor_ids:
            continue

        task_id = task["Id"]
        lifetime_data = await get_task_lifetime(db_user.is_password_b64, task_id)

        events = []
        if isinstance(lifetime_data, list):
            events = lifetime_data
        elif (
            isinstance(lifetime_data, dict)
            and "TaskLifetimes" in lifetime_data
        ):
            events = lifetime_data["TaskLifetimes"]

        if events:
            for event in events:
                notif = _process_lifetime_event(
                    event,
                    task,
                    db_user,
                    base_web_url,
                    last_check_time_local,
                    intraservice_tz,
                    updated_statuses_map,
                )
                if notif:
                    notifications.append(notif)

    return notifications

async def _publish_pending_notifications(
    redis_client: aioredis.Redis,
    notifications: list[dict],
    tg_id: int,
) -> None:
    """
    Публикует накопленные уведомления в Redis.
    """
    for payload in notifications:
        try:
            await redis_client.publish(
                "intraservice_events", json.dumps(payload)
            )
        except Exception as pub_err:
            logger.error(
                "Не удалось опубликовать уведомление в Redis для пользователя %s, "
                "событие %s, заявка %s: %s. Payload: %s",
                tg_id,
                payload.get("event_type"),
                payload.get("task_id"),
                pub_err,
                json.dumps(payload),
            )

async def process_user(  # noqa: PLR0913
    user_id: int,
    redis_client: aioredis.Redis,
    base_web_url: str,
    semaphore: asyncio.Semaphore,
    current_time_utc: datetime,
    intraservice_tz: ZoneInfo,
):
    """
    Обрабатывает обновления для одного пользователя под семафором
    и в рамках отдельной сессии БД.
    """
    async with semaphore, AsyncSessionLocal() as db:
        try:
            # Получаем свежий экземпляр пользователя, привязанный к текущей сессии
            db_user = await db.get(User, user_id)
            if not db_user:
                return

            tg_id = db_user.tg_user_id
            is_user_id = db_user.is_user_id
            auth_b64 = db_user.is_password_b64

            if not tg_id or not is_user_id or not auth_b64:
                return

            # Инициализация / получение временных рамок проверки
            times_info = await _get_api_filter_time(
                db_user, db, current_time_utc, intraservice_tz
            )
            if times_info is None:
                return

            last_check_time_local, api_filter_time = times_info

            # 1. Проверка НОВЫХ заявок
            new_notifications = await _check_new_tasks(
                db_user, auth_b64, api_filter_time, base_web_url
            )
            if new_notifications is None:
                return

            # 2. Проверка КОММЕНТАРИЕВ и изменений статуса
            updated_notifications = await _check_task_updates(
                db_user,
                api_filter_time,
                base_web_url,
                last_check_time_local,
                intraservice_tz,
            )
            if updated_notifications is None:
                return

            pending_notifications = new_notifications + updated_notifications

            # Обновляем время последней проверки на текущее UTC
            db_user.last_check_time = current_time_utc.strftime("%Y-%m-%d %H:%M:%S")

            # Фиксируем состояние в БД
            await db.commit()

            # Только если коммит прошел успешно, отправляем сообщения в Redis
            await _publish_pending_notifications(
                redis_client, pending_notifications, tg_id
            )

        except Exception as e:
            logger.error(
                "Ошибка при обработке обновлений для пользователя %s: %s",
                user_id,
                e,
            )
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
    current_time_utc = datetime.now(UTC)

    # Используем отдельную сессию для потокового чтения
    async with AsyncSessionLocal() as stream_db:
        try:
            # Читаем только tg_user_id пользователей батчами (yield_per)
            query = select(User.tg_user_id).execution_options(yield_per=100)
            result = await stream_db.stream(query)

            async for partition in result.partitions(100):
                tasks = []
                for row in partition:
                    tg_id = row[0]
                    # Передаем только tg_user_id.
                    # Сессия будет открыта внутри process_user под семафором.
                    tasks.append(
                        process_user(
                            user_id=tg_id,
                            redis_client=redis,
                            base_web_url=base_web_url,
                            semaphore=semaphore,
                            current_time_utc=current_time_utc,
                            intraservice_tz=intraservice_tz,
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
        coalesce=True,
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
