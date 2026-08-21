import base64
import logging
from datetime import datetime
from http import HTTPStatus
from typing import Any

import aiohttp

from app.config import settings
from app.services.crypto import decrypt_token
from app.utils.date_utils import parse_api_date

logger = logging.getLogger(__name__)


_session: aiohttp.ClientSession | None = None


async def init_session() -> None:
    """
    Инициализирует глобальную сессию aiohttp.ClientSession.
    """
    global _session  # noqa: PLW0603
    if _session is None or _session.closed:
        # TODO(security): Проверить SSL_VERIFY в соответствии с правилами
        # безопасной разработки.
        # Внутренние домены могут требовать отключения проверки SSL в тестовой среде.
        connector = aiohttp.TCPConnector(ssl=settings.SSL_VERIFY)
        _session = aiohttp.ClientSession(connector=connector)


async def close_session() -> None:
    """
    Закрывает глобальную сессию aiohttp.ClientSession.
    """
    global _session  # noqa: PLW0603
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def _make_request(  # noqa: PLR0913
    endpoint: str,
    method: str = "GET",
    auth_b64: str | None = None,
    auth_header: str | None = None,
    params: dict[str, Any] | None = None,
    json_data: dict[str, Any] | None = None,
) -> Any | None:
    """
    Универсальная функция для выполнения HTTP-запросов к API IntraService.
    """
    url = f"{settings.INTRASERVICE_URL.rstrip('/')}/{endpoint.lstrip('/')}"

    headers = {"Content-Type": "application/json"}
    if auth_b64:
        decrypted_auth = decrypt_token(auth_b64)
        headers["Authorization"] = f"Basic {decrypted_auth}"
    elif auth_header:
        headers["Authorization"] = auth_header

    # Ленивая инициализация, если сессия еще не создана
    if _session is None or _session.closed:
        await init_session()

    assert _session is not None

    try:
        async with _session.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            if response.status in (HTTPStatus.OK, HTTPStatus.CREATED):
                try:
                    return await response.json()
                except Exception:
                    return {}
            elif response.status == HTTPStatus.NO_CONTENT:
                return {}
            
            text = await response.text()
            logger.error("Ошибка API [%d] для %s: %s", response.status, endpoint, text)
            return None
    except Exception as e:
        logger.exception("Сетевая ошибка при запросе к API %s: %s", endpoint, e)
        return None


async def verify_credentials(
    login: str, password: str
) -> tuple[str | None, int | None]:
    """
    Проверяет учетные данные пользователя в IntraService.
    Возвращает (auth_b64, user_id) при успехе, иначе (None, None).
    """
    auth_header = aiohttp.BasicAuth(login, password).encode()

    response_data = await _make_request(
        endpoint="user",
        method="GET",
        auth_header=auth_header,
        params={"getcurrentuserinfo": "true"},
    )

    if response_data is not None:
        auth_str = f"{login}:{password}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        user_id = response_data.get("Id")
        return auth_b64, user_id
    return None, None


async def get_tasks(auth_b64: str, filters: dict[str, Any] | None = None) -> Any | None:
    """
    Получает список задач из IntraService.
    """
    params = {"include": "status"}
    if filters:
        params.update(filters)

    return await _make_request(
        endpoint="task", method="GET", auth_b64=auth_b64, params=params
    )


async def get_task_lifetime(auth_b64: str, task_id: int) -> list[dict[str, Any]] | None:
    """
    Получает историю изменений (lifetime) задачи.
    """
    return await _make_request(
        endpoint="tasklifetime",
        method="GET",
        auth_b64=auth_b64,
        params={"taskid": task_id},
    )


async def get_statuses(auth_b64: str) -> list[dict[str, Any]] | None:
    """
    Получает справочник статусов задач.
    """
    return await _make_request(endpoint="taskstatus", method="GET", auth_b64=auth_b64)


async def get_services(auth_b64: str) -> list[dict[str, Any]] | None:
    """
    Получает каталог услуг из IntraService.
    """
    return await _make_request(
        endpoint="service",
        method="GET",
        auth_b64=auth_b64,
        params={"include": "parentid", "for": "createtask", "pagesize": "1000"},
    )


async def get_single_task(auth_b64: str, task_id: int) -> dict[str, Any] | None:
    """
    Получает детальную информацию по конкретной задаче с кастомными полями.
    Используется printer-worker для Fast-Track маршрутизации.
    """
    return await _make_request(
        endpoint=f"task/{task_id}",
        method="GET",
        auth_b64=auth_b64,
        params={"include": "customfields,status"},
    )


async def add_task_comment(auth_b64: str, task_id: int, comment: str) -> bool:
    """
    Добавляет комментарий к задаче в IntraService.
    """
    res = await _make_request(
        endpoint=f"task/{task_id}",
        method="PUT",
        auth_b64=auth_b64,
        json_data={"Id": task_id, "Comment": comment, "IsPrivateComment": False},
    )
    return res is not None


async def update_task_status(auth_b64: str, task_id: int, status_id: int) -> bool:
    """
    Обновляет статус задачи в IntraService.
    """
    res = await _make_request(
        endpoint=f"task/{task_id}",
        method="PUT",
        auth_b64=auth_b64,
        json_data={"Id": task_id, "StatusId": status_id},
    )
    return res is not None


async def add_task_expenses(auth_b64: str, task_id: int, minutes: int) -> bool:
    """
    Добавляет трудозатраты к задаче в IntraService.
    """
    res = await _make_request(
        endpoint="taskexpenses",
        method="POST",
        auth_b64=auth_b64,
        json_data={"TaskId": task_id, "Minutes": minutes},
    )
    return res is not None


async def get_tasks_by_status(
    auth_b64: str, status_id: int, page: int = 1, page_size: int = 100
) -> Any | None:
    """
    Получает список задач по ID статуса.
    """
    params = {
        "include": "status,customfields,service",
        "StatusIds": str(status_id),
        "page": page,
        "pageSize": page_size,
    }
    return await _make_request(
        endpoint="task", method="GET", auth_b64=auth_b64, params=params
    )


async def get_task_comments(auth_b64: str, task_id: int) -> list[dict[str, Any]] | None:
    """
    Получает историю изменений задачи (lifetime) для анализа комментариев.
    """
    return await get_task_lifetime(auth_b64, task_id)


async def update_task_custom_fields(
    auth_b64: str, task_id: int, custom_field_values: list[dict[str, Any]]
) -> bool:
    """
    Обновляет кастомные поля задачи в IntraService.
    """
    res = await _make_request(
        endpoint=f"task/{task_id}",
        method="PUT",
        auth_b64=auth_b64,
        json_data={"Id": task_id, "CustomFieldValues": custom_field_values},
    )
    return res is not None
