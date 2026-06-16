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

async def _check_new_tasks_global(
    new_tasks: list[dict],
    statuses_map: dict,
    users_by_is_id: dict[str, User],
    base_web_url: str,
) -> list[dict]:
    """
    Проверяет новые задачи и возвращает список событий для отправки в Redis.
    """
    notifications = []
    for task in new_tasks:
        executor_ids_str = str(task.get("ExecutorIds") or "")
        executor_ids = [
            eid.strip()
            for eid in executor_ids_str.split(",")
            if eid.strip()
        ]
        for exec_id in executor_ids:
            if exec_id in users_by_is_id:
                db_user = users_by_is_id[exec_id]
                last_task_id = db_user.last_task_id or 0
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
                    db_user.last_task_id = max(db_user.last_task_id or 0, task["Id"])
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


async def _check_task_updates_global(  # noqa: PLR0913
    updated_tasks: list[dict],
    updated_statuses_map: dict,
    users_by_is_id: dict[str, User],
    base_web_url: str,
    last_check_time_local: datetime,
    intraservice_tz: ZoneInfo,
    service_auth_b64: str,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """
    Проверяет изменения в обновленных задачах (комментарии, статусы) и возвращает уведомления.
    """
    notifications = []
    tasks_to_check = []
    for task in updated_tasks:
        executor_ids_str = str(task.get("ExecutorIds") or "")
        executor_ids = [
            eid.strip()
            for eid in executor_ids_str.split(",")
            if eid.strip()
        ]
        has_our_executors = any(exec_id in users_by_is_id for exec_id in executor_ids)
        if has_our_executors:
            tasks_to_check.append((task, executor_ids))

    if not tasks_to_check:
        return notifications

    async def check_single_task_history(task: dict, executor_ids: list[str]):
        task_id = task["Id"]
        async with semaphore:
            lifetime_data = await get_task_lifetime(service_auth_b64, task_id)

        events = []
        if isinstance(lifetime_data, list):
            events = lifetime_data
        elif isinstance(lifetime_data, dict) and "TaskLifetimes" in lifetime_data:
            events = lifetime_data["TaskLifetimes"]

        task_notifications = []
        if events:
            for event in events:
                for exec_id in executor_ids:
                    if exec_id in users_by_is_id:
                        db_user = users_by_is_id[exec_id]
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
                            task_notifications.append(notif)
        return task_notifications

    history_tasks = [check_single_task_history(t, execs) for t, execs in tasks_to_check]
    results = await asyncio.gather(*history_tasks)
    for res in results:
        notifications.extend(res)

    return notifications


async def process_user(
    user_id: int,
    redis_client,
    base_web_url: str,
    semaphore: asyncio.Semaphore,
    current_time_utc: datetime,
    intraservice_tz: ZoneInfo,
) -> None:
    """
    [DEPRECATED] Обрабатывает заявки для одного конкретного пользователя.
    Используется для обратной совместимости в юнит-тестах.
    """
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if not user:
            logger.warning("Пользователь с ID %s не найден в БД", user_id)
            return

        if not user.is_password_b64:
            logger.warning("У пользователя %s отсутствует пароль IntraService", user_id)
            return

        if not user.last_check_time:
            user.last_check_time = current_time_utc.strftime("%Y-%m-%d %H:%M:%S")
            await db.commit()
            logger.info("Для пользователя %s инициализировано время последней проверки", user_id)
            return

        last_check_time_naive = parse_api_date(user.last_check_time)
        if not last_check_time_naive:
            last_check_time_naive = current_time_utc

        # Переводим во временную зону IntraService
        last_check_time_local = last_check_time_naive.replace(tzinfo=UTC).astimezone(intraservice_tz)
        api_filter_time = last_check_time_local.strftime("%Y-%m-%d %H:%M")

        # 1. Запрос новых заявок
        new_tasks_data = await get_tasks(
            user.is_password_b64,
            {"CreatedMoreThan": api_filter_time, "include": "executorids,status"},
        )
        if new_tasks_data is None:
            logger.warning("Не удалось получить новые заявки для пользователя %s", user_id)
            return

        # 2. Запрос измененных заявок
        updated_tasks_data = await get_tasks(
            user.is_password_b64,
            {"ChangedMoreThan": api_filter_time, "include": "executorids,status"},
        )
        if updated_tasks_data is None:
            logger.warning("Не удалось получить обновленные заявки для пользователя %s", user_id)
            return

        # Парсим новые задачи
        new_tasks = []
        statuses_map = {}
        if isinstance(new_tasks_data, dict):
            new_tasks = new_tasks_data.get("Tasks", [])
            for s in new_tasks_data.get("Statuses", []):
                statuses_map[s.get("Id")] = s.get("Name")
        elif isinstance(new_tasks_data, list):
            new_tasks = new_tasks_data

        # Парсим измененные задачи
        updated_tasks = []
        updated_statuses_map = {}
        if isinstance(updated_tasks_data, dict):
            updated_tasks = updated_tasks_data.get("Tasks", [])
            for s in updated_tasks_data.get("Statuses", []):
                updated_statuses_map[s.get("Id")] = s.get("Name")
        elif isinstance(updated_tasks_data, list):
            updated_tasks = updated_tasks_data

        users_by_is_id = {str(user.is_user_id): user}

        # Вызываем _check_new_tasks_global
        new_notifications = await _check_new_tasks_global(
            new_tasks, statuses_map, users_by_is_id, base_web_url
        )

        # Вызываем _check_task_updates_global
        updated_notifications = await _check_task_updates_global(
            updated_tasks,
            updated_statuses_map,
            users_by_is_id,
            base_web_url,
            last_check_time_local,
            intraservice_tz,
            user.is_password_b64,
            semaphore,
        )

        pending_notifications = new_notifications + updated_notifications

        # Сохраняем изменения пользователя в БД
        user.last_check_time = current_time_utc.strftime("%Y-%m-%d %H:%M:%S")
        await db.commit()

        # Публикуем уведомления
        if pending_notifications:
            for payload in pending_notifications:
                try:
                    await redis_client.publish("intraservice_events", json.dumps(payload))
                except Exception as pub_err:
                    logger.error(
                        "Ошибка отправки уведомления в Redis для пользователя %s: %s",
                        payload.get("tg_user_id"),
                        pub_err,
                    )


async def check_updates():
    """
    Периодическая проверка новых заявок и комментариев на стороне Core API.
    Использует выделенный сервисный аккаунт IntraService.
    """
    if not settings.INTRASERVICE_SERVICE_LOGIN or not settings.INTRASERVICE_SERVICE_PASSWORD:
        logger.error(
            "Сервисный аккаунт IntraService не настроен! "
            "Задайте INTRASERVICE_SERVICE_LOGIN и INTRASERVICE_SERVICE_PASSWORD."
        )
        return

    # Импорты внутри для избежания циклических зависимостей
    import base64
    from app.services.crypto import encrypt_token

    # Готовим авторизацию для сервисного аккаунта
    auth_str = f"{settings.INTRASERVICE_SERVICE_LOGIN}:{settings.INTRASERVICE_SERVICE_PASSWORD}"
    plain_b64 = base64.b64encode(auth_str.encode()).decode()
    service_auth_b64 = encrypt_token(plain_b64)

    base_web_url = settings.INTRASERVICE_URL.replace("/api/", "")
    redis = get_redis_client()

    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)
    intraservice_tz = ZoneInfo(settings.INTRASERVICE_TZ)
    current_time_utc = datetime.now(UTC)

    # 1. Получаем время последней проверки из Redis
    last_check_time_str = await redis.get("worker:last_check_time")
    if last_check_time_str:
        last_check_time_utc = parse_api_date(last_check_time_str)
        if last_check_time_utc:
            if last_check_time_utc.tzinfo is None:
                last_check_time_utc = last_check_time_utc.replace(tzinfo=UTC)
        else:
            last_check_time_utc = current_time_utc
    else:
        last_check_time_utc = current_time_utc
        await redis.set("worker:last_check_time", current_time_utc.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("Первичный запуск. Время последней проверки инициализировано: %s", current_time_utc)
        return

    last_check_time_local = last_check_time_utc.astimezone(intraservice_tz)
    api_filter_time = last_check_time_local.strftime("%Y-%m-%d %H:%M")

    # 2. Получаем список всех пользователей из локальной БД
    async with AsyncSessionLocal() as db:
        try:
            query = select(User)
            result = await db.execute(query)
            users = result.scalars().all()

            if not users:
                # Нет зарегистрированных пользователей, некого уведомлять
                # Обновим время последней проверки, чтобы не накапливать интервал
                await redis.set("worker:last_check_time", current_time_utc.strftime("%Y-%m-%d %H:%M:%S"))
                return

            users_by_is_id = {str(u.is_user_id): u for u in users if u.is_user_id}
            if not users_by_is_id:
                await redis.set("worker:last_check_time", current_time_utc.strftime("%Y-%m-%d %H:%M:%S"))
                return

            # 3. Запросы к IntraService от имени сервисного аккаунта
            new_tasks_data = await get_tasks(
                service_auth_b64,
                {"CreatedMoreThan": api_filter_time, "include": "executorids,status"},
            )

            updated_tasks_data = await get_tasks(
                service_auth_b64,
                {"ChangedMoreThan": api_filter_time, "include": "executorids,status"},
            )

            # Парсим новые задачи
            new_tasks = []
            statuses_map = {}
            if new_tasks_data is not None:
                if isinstance(new_tasks_data, dict):
                    new_tasks = new_tasks_data.get("Tasks", [])
                    for s in new_tasks_data.get("Statuses", []):
                        statuses_map[s.get("Id")] = s.get("Name")
                elif isinstance(new_tasks_data, list):
                    new_tasks = new_tasks_data

            # Парсим измененные задачи
            updated_tasks = []
            updated_statuses_map = {}
            if updated_tasks_data is not None:
                if isinstance(updated_tasks_data, dict):
                    updated_tasks = updated_tasks_data.get("Tasks", [])
                    for s in updated_tasks_data.get("Statuses", []):
                        updated_statuses_map[s.get("Id")] = s.get("Name")
                elif isinstance(updated_tasks_data, list):
                    updated_tasks = updated_tasks_data

            # 4. Проверяем новые
            new_notifications = await _check_new_tasks_global(
                new_tasks, statuses_map, users_by_is_id, base_web_url
            )

            # 5. Проверяем изменения
            updated_notifications = await _check_task_updates_global(
                updated_tasks,
                updated_statuses_map,
                users_by_is_id,
                base_web_url,
                last_check_time_local,
                intraservice_tz,
                service_auth_b64,
                semaphore,
            )

            pending_notifications = new_notifications + updated_notifications

            # Сохраняем измененные last_task_id пользователей в БД
            await db.commit()

            # Обновляем время последней проверки в Redis
            await redis.set("worker:last_check_time", current_time_utc.strftime("%Y-%m-%d %H:%M:%S"))

            # Публикуем уведомления в Redis
            if pending_notifications:
                for payload in pending_notifications:
                    try:
                        await redis.publish("intraservice_events", json.dumps(payload))
                    except Exception as pub_err:
                        logger.error(
                            "Ошибка отправки уведомления в Redis для пользователя %s: %s",
                            payload.get("tg_user_id"),
                            pub_err,
                        )
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
