import asyncio
import json
import logging
from typing import Any
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.config import settings
from app.database.db import AsyncSessionLocal, User
from app.services.intraservice import (
    get_task_lifetime,
    get_tasks,
    parse_api_date,
    get_tasks_by_status,
    get_task_comments,
    update_task_custom_fields,
    update_task_status,
    get_services,
)

logger = logging.getLogger(__name__)

redis_client = None
scheduler = AsyncIOScheduler()


class VirtualServiceUser:
    def __init__(self, is_user_id: int, is_login: str, last_task_id: int = 0):
        self.tg_user_id = None
        self.is_user_id = is_user_id
        self.is_login = is_login
        self.last_task_id = last_task_id


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
    users_by_is_id: dict[str, Any],
    base_web_url: str,
) -> list[dict]:
    """
    Проверяет новые задачи и возвращает список событий для отправки в Redis.
    """
    notifications = []
    for task in new_tasks:
        if task.get("_is_redirected"):
            continue
        executor_ids_str = str(task.get("ExecutorIds") or "")
        executor_ids = [
            eid.strip() for eid in executor_ids_str.split(",") if eid.strip()
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
                        "task_data": task,
                    }

                    notifications.append(payload)
                    db_user.last_task_id = max(db_user.last_task_id or 0, task["Id"])
    return notifications


def _process_lifetime_event(  # noqa: PLR0913
    event: dict,
    task: dict,
    db_user: Any,
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
            "task_data": task,
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
            "task_data": task,
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
            "status_id": int(task.get("StatusId"))
            if task.get("StatusId") is not None
            else None,
            "task_data": task,
        }

    return None


async def _check_task_updates_global(  # noqa: PLR0913
    updated_tasks: list[dict],
    updated_statuses_map: dict,
    users_by_is_id: dict[str, Any],
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
            eid.strip() for eid in executor_ids_str.split(",") if eid.strip()
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
            logger.info(
                "Для пользователя %s инициализировано время последней проверки", user_id
            )
            return

        last_check_time_naive = parse_api_date(user.last_check_time)
        if not last_check_time_naive:
            last_check_time_naive = current_time_utc

        # Переводим во временную зону IntraService
        last_check_time_local = last_check_time_naive.replace(tzinfo=UTC).astimezone(
            intraservice_tz
        )
        api_filter_time = last_check_time_local.strftime("%Y-%m-%d %H:%M")

        # 1. Запрос новых заявок
        new_tasks_data = await get_tasks(
            user.is_password_b64,
            {
                "CreatedMoreThan": api_filter_time,
                "include": "executorids,status,customfields",
            },
        )
        if new_tasks_data is None:
            logger.warning(
                "Не удалось получить новые заявки для пользователя %s", user_id
            )
            return

        # 2. Запрос измененных заявок
        updated_tasks_data = await get_tasks(
            user.is_password_b64,
            {
                "ChangedMoreThan": api_filter_time,
                "include": "executorids,status,customfields",
            },
        )
        if updated_tasks_data is None:
            logger.warning(
                "Не удалось получить обновленные заявки для пользователя %s", user_id
            )
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
                    await redis_client.publish(
                        "intraservice_events", json.dumps(payload)
                    )
                except Exception as pub_err:
                    logger.error(
                        "Ошибка отправки уведомления в Redis для пользователя %s: %s",
                        payload.get("tg_user_id"),
                        pub_err,
                    )


async def check_waiting_printer_tasks(
    service_auth_b64: str,
    redis,
    semaphore: asyncio.Semaphore,
    users_by_is_id: dict,
) -> None:
    """
    Периодически проверяет задачи в статусе STATUS_WAITING_ID (35),
    анализирует новые комментарии от пользователей, извлекает сетевые данные (IP / имя ПК)
    и возвращает задачи в работу (статус STATUS_OPEN_ID=31), обновляя кастомные поля.
    """
    import re

    IP_PATTERN = re.compile(r"\b(10\.(?:244|245)\.\d{1,3}\.\d{1,3})\b")
    PC_PREFIXES = r"(?:[KК][ZЗ][MМ]|[KК][MМ][KК]|[TТ][LЛ][KК]|[TТ][KК][TТ]|[TТ][NН][TТ]|[IИ][TТ][TТ]|[TТ][NН][MМ]|[GГ][KК][TТ])"
    PC_NAME_PATTERN = re.compile(rf"\b({PC_PREFIXES}\d{{4}})\b", re.IGNORECASE)
    PRINTER_NAME_PATTERN = re.compile(rf"\b({PC_PREFIXES}[PП]\d{{4}})\b", re.IGNORECASE)

    def normalize_device_name(name: str) -> str:
        trans_map = str.maketrans("КЗМПТЛНИГкзмптлниг", "KZMPTLNIGkzmptlnig")
        return name.upper().translate(trans_map)

    def extract_network_data(comment_text: str) -> dict:
        result = {}
        ip_match = IP_PATTERN.search(comment_text)
        if ip_match:
            result["printer_address"] = ip_match.group(1)

        printer_name_match = PRINTER_NAME_PATTERN.search(comment_text)
        if printer_name_match:
            result["printer_address"] = normalize_device_name(
                printer_name_match.group(1)
            )

        pc_match = PC_NAME_PATTERN.search(comment_text)
        if pc_match:
            result["target_pc"] = normalize_device_name(pc_match.group(1))

        return result

    # 1. Загружаем задачи в статусе 35
    async with semaphore:
        tasks_data = await get_tasks_by_status(
            service_auth_b64, settings.STATUS_WAITING_ID
        )

    if not tasks_data:
        return

    tasks = []
    if isinstance(tasks_data, dict):
        tasks = tasks_data.get("Tasks", [])
    elif isinstance(tasks_data, list):
        tasks = tasks_data

    for task in tasks:
        task_id = task.get("Id")
        if not task_id:
            continue

        # Проверяем, назначена ли задача на наших пользователей (включая воркера)
        executor_ids_str = str(task.get("ExecutorIds") or "")
        executor_ids = [
            eid.strip() for eid in executor_ids_str.split(",") if eid.strip()
        ]
        has_our_executors = any(exec_id in users_by_is_id for exec_id in executor_ids)
        if not has_our_executors:
            continue

        # Защита от повторной обработки
        redis_key = f"printer_resumed:{task_id}"
        is_processed = await redis.get(redis_key)
        if is_processed:
            continue

        # 2. Получаем историю изменений для поиска комментариев
        async with semaphore:
            lifetime_data = await get_task_comments(service_auth_b64, task_id)

        events = []
        if isinstance(lifetime_data, list):
            events = lifetime_data
        elif isinstance(lifetime_data, dict) and "TaskLifetimes" in lifetime_data:
            events = lifetime_data["TaskLifetimes"]

        if not events:
            continue

        # Ищем последний комментарий (первый с конца, т.к. список от новых к старым)
        last_comment_event = None
        for event in events:
            if event.get("Comments"):
                last_comment_event = event
                break

        if not last_comment_event:
            continue

        # Проверяем, не воркер ли автор комментария
        editor_id = last_comment_event.get("EditorId")
        editor_name = last_comment_event.get("Editor") or ""

        is_service_comment = False
        if (
            settings.INTRASERVICE_SERVICE_USER_ID
            and editor_id == settings.INTRASERVICE_SERVICE_USER_ID
        ):
            is_service_comment = True
        elif (
            settings.INTRASERVICE_SERVICE_LOGIN
            and settings.INTRASERVICE_SERVICE_LOGIN.lower() in editor_name.lower()
        ):
            is_service_comment = True

        if is_service_comment:
            continue

        # Извлекаем сетевые данные из комментария пользователя
        comment_text = last_comment_event.get("Comments") or ""
        extracted = extract_network_data(comment_text)
        if not extracted:
            continue

        logger.info(
            "Найдена полезная информация в комментарии к задаче #%d: %s. Начинаем возобновление.",
            task_id,
            extracted,
        )

        # 3. Формируем поля для обновления
        fields_to_update = []
        if "printer_address" in extracted:
            fields_to_update.append(
                {
                    "FieldId": settings.PRINTER_IP_CUSTOM_FIELD_ID,
                    "Value": extracted["printer_address"],
                }
            )
        if "target_pc" in extracted:
            fields_to_update.append(
                {
                    "FieldId": settings.PRINTER_PC_CUSTOM_FIELD_ID,
                    "Value": extracted["target_pc"],
                }
            )

        # Обновляем поля
        async with semaphore:
            fields_ok = await update_task_custom_fields(
                service_auth_b64, task_id, fields_to_update
            )

        if not fields_ok:
            logger.error("Не удалось обновить кастомные поля для задачи #%d", task_id)
            continue

        # 4. Переводим задачу в статус "Открыта"
        async with semaphore:
            status_ok = await update_task_status(
                service_auth_b64, task_id, settings.STATUS_OPEN_ID
            )

        if not status_ok:
            logger.error("Не удалось изменить статус задачи #%d на 'Открыта'", task_id)
            continue

        # 5. Сохраняем в Redis, чтобы избежать бесконечного цикла
        await redis.set(redis_key, str(datetime.now(UTC)), ex=86400)
        logger.info(
            "Задача #%d успешно переведена в статус 'Открыта' с новыми параметрами",
            task_id,
        )


async def sync_service_catalog() -> None:
    """
    Получает каталог услуг из IntraService и сохраняет его в Redis в плоском виде.
    """
    logger.info("Синхронизация каталога услуг...")
    redis = get_redis_client()

    # Получаем учетные данные сервисного аккаунта
    import base64
    from app.services.crypto import encrypt_token

    raw_auth = None
    if settings.INTRASERVICE_SERVICE_LOGIN and settings.INTRASERVICE_SERVICE_PASSWORD:
        auth_str = f"{settings.INTRASERVICE_SERVICE_LOGIN}:{settings.INTRASERVICE_SERVICE_PASSWORD}"
        plain_b64 = base64.b64encode(auth_str.encode()).decode()
        raw_auth = encrypt_token(plain_b64)
    else:
        raw_auth = await redis.get("worker:service_auth_b64")

    if not raw_auth:
        logger.warning(
            "Не удалось выполнить синхронизацию каталога услуг: отсутствуют учетные данные."
        )
        return

    if isinstance(raw_auth, bytes):
        service_auth_b64: str = raw_auth.decode()
    else:
        service_auth_b64: str = raw_auth

    try:
        services = await get_services(service_auth_b64)
        if not services:
            logger.warning("Каталог услуг пуст или не удалось его получить.")
            return

        services_list = []
        if isinstance(services, dict):
            services_list = services.get("Services") or []
        elif isinstance(services, list):
            services_list = services

        # Формируем плоский список услуг с ID, Name, ParentId
        flat_catalog = []
        excluded_ids = set(settings.EXCLUDED_SERVICE_IDS)

        # Функция для проверки, нужно ли исключить сервис (включая дочерние)
        # Если сервис или любой из его родителей в исключенных - пропускаем
        def is_excluded(svc_id: int) -> bool:
            current_id = svc_id
            while current_id:
                if current_id in excluded_ids:
                    return True
                # Найти родителя
                parent = next(
                    (s for s in services_list if s.get("Id") == current_id), None
                )
                if not parent:
                    break
                current_id = parent.get("ParentId")
            return False

        for svc in services_list:
            if is_excluded(svc.get("Id")):
                continue
            flat_catalog.append(
                {
                    "id": svc.get("Id"),
                    "name": svc.get("Name"),
                    "parent_id": svc.get("ParentId"),
                    "path": svc.get("Path"),
                }
            )

        # Сохраняем в Redis с TTL 24 часа
        await redis.set(
            "worker:service_catalog",
            json.dumps(flat_catalog, ensure_ascii=False),
            ex=86400,
        )
        logger.info(
            "Каталог услуг успешно синхронизирован в Redis (%d элементов).",
            len(flat_catalog),
        )
    except Exception as e:
        logger.exception("Ошибка при синхронизации каталога услуг: %s", e)


async def check_updates():
    """
    Периодическая проверка новых заявок и комментариев на стороне Core API.
    Использует выделенный сервисный аккаунт IntraService или учетные данные,
    сохраненные при авторизации в веб-панели.
    """
    # Импорты внутри для избежания циклических зависимостей
    import base64
    from app.services.crypto import encrypt_token

    redis = get_redis_client()

    # Сначала пытаемся взять данные из настроек (переменных окружения)
    raw_auth = None
    if settings.INTRASERVICE_SERVICE_LOGIN and settings.INTRASERVICE_SERVICE_PASSWORD:
        auth_str = f"{settings.INTRASERVICE_SERVICE_LOGIN}:{settings.INTRASERVICE_SERVICE_PASSWORD}"
        plain_b64 = base64.b64encode(auth_str.encode()).decode()
        raw_auth = encrypt_token(plain_b64)
    else:
        # Пытаемся получить сохраненные учетные данные администратора из Redis
        raw_auth = await redis.get("worker:service_auth_b64")

    if not raw_auth:
        logger.warning(
            "Сервисный аккаунт IntraService не настроен! "
            "Пожалуйста, авторизуйтесь в веб-панели или задайте "
            "INTRASERVICE_SERVICE_LOGIN и INTRASERVICE_SERVICE_PASSWORD в .env."
        )
        return

    if isinstance(raw_auth, bytes):
        service_auth_b64: str = raw_auth.decode()
    else:
        service_auth_b64: str = raw_auth

    base_web_url = settings.INTRASERVICE_URL.replace("/api/", "")

    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)
    intraservice_tz = ZoneInfo(settings.INTRASERVICE_TZ)
    current_time_utc = datetime.now(UTC)

    # 1. Получаем время последней проверки из Redis
    raw_last_check = await redis.get("worker:last_check_time")
    if raw_last_check:
        last_check_time_str = (
            raw_last_check.decode()
            if isinstance(raw_last_check, bytes)
            else raw_last_check
        )
        last_check_time_utc = parse_api_date(last_check_time_str)
        if last_check_time_utc:
            if last_check_time_utc.tzinfo is None:
                last_check_time_utc = last_check_time_utc.replace(tzinfo=UTC)
        else:
            last_check_time_utc = current_time_utc
    else:
        last_check_time_utc = current_time_utc
        await redis.set(
            "worker:last_check_time", current_time_utc.strftime("%Y-%m-%d %H:%M:%S")
        )
        logger.info(
            "Первичный запуск. Время последней проверки инициализировано: %s",
            current_time_utc,
        )
        return

    last_check_time_local = last_check_time_utc.astimezone(intraservice_tz)
    api_filter_time = last_check_time_local.strftime("%Y-%m-%d %H:%M")

    # 2. Получаем список всех пользователей из локальной БД
    async with AsyncSessionLocal() as db:
        try:
            query = select(User)
            result = await db.execute(query)
            users = result.scalars().all()

            users_by_is_id: dict[str, Any] = {
                str(u.is_user_id): u for u in users if u.is_user_id
            }

            service_user = None
            if settings.INTRASERVICE_SERVICE_USER_ID:
                service_last_task_id_str = await redis.get(
                    "worker:service_last_task_id"
                )
                try:
                    service_last_task_id = (
                        int(service_last_task_id_str) if service_last_task_id_str else 0
                    )
                except ValueError:
                    service_last_task_id = 0

                service_user = VirtualServiceUser(
                    is_user_id=settings.INTRASERVICE_SERVICE_USER_ID,
                    is_login=settings.INTRASERVICE_SERVICE_LOGIN or "service",
                    last_task_id=service_last_task_id,
                )
                users_by_is_id[str(settings.INTRASERVICE_SERVICE_USER_ID)] = (
                    service_user
                )

            if not users_by_is_id:
                # Нет зарегистрированных пользователей и не задан сервисный аккаунт
                await redis.set(
                    "worker:last_check_time",
                    current_time_utc.strftime("%Y-%m-%d %H:%M:%S"),
                )
                return

            # 3. Запросы к IntraService от имени сервисного аккаунта
            new_tasks_data = await get_tasks(
                service_auth_b64,
                {
                    "CreatedMoreThan": api_filter_time,
                    "include": "executorids,status,customfields",
                },
            )

            updated_tasks_data = await get_tasks(
                service_auth_b64,
                {
                    "ChangedMoreThan": api_filter_time,
                    "include": "executorids,status,customfields",
                },
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

            # Проверяем зависшие принтерные задачи
            try:
                await check_waiting_printer_tasks(
                    service_auth_b64, redis, semaphore, users_by_is_id
                )
            except Exception as e_waiting:
                logger.exception(
                    "Ошибка при обработке зависших принтерных задач: %s", e_waiting
                )

            # Сохраняем last_task_id сервисного аккаунта в Redis
            if service_user:
                await redis.set(
                    "worker:service_last_task_id", service_user.last_task_id
                )

            # Обновляем время последней проверки в Redis
            await redis.set(
                "worker:last_check_time", current_time_utc.strftime("%Y-%m-%d %H:%M:%S")
            )

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

    # Первичная синхронизация каталога услуг при старте
    try:
        await sync_service_catalog()
    except Exception as e:
        logger.error("Первичная синхронизация каталога услуг завершилась сбоем: %s", e)

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

    # Ежедневная синхронизация каталога услуг
    scheduler.add_job(
        sync_service_catalog,
        "interval",
        days=1,
        id="sync_service_catalog_job",
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
