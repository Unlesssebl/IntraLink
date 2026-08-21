import logging
from datetime import datetime
from http import HTTPStatus
from typing import Any, Dict, List, Optional

import aiohttp

from core.config import settings
from core.crypto import decrypt_token
from core.date_utils import parse_api_date

logger = logging.getLogger(__name__)

_session: Optional[aiohttp.ClientSession] = None


async def init_session() -> None:
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(ssl=settings.SSL_VERIFY)
        _session = aiohttp.ClientSession(connector=connector)


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        await init_session()
    assert _session is not None
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def _make_direct_request(
    endpoint: str,
    method: str = "GET",
    auth_b64: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Прямой запрос к API IntraService.
    """
    url = f"{settings.INTRASERVICE_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {"Content-Type": "application/json"}

    if auth_b64:
        decrypted_auth = decrypt_token(auth_b64)
        headers["Authorization"] = f"Basic {decrypted_auth}"

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
            if response.status == HTTPStatus.OK:
                return await response.json()
            text = await response.text()
            logger.error(
                "Ошибка direct API [%d] для %s: %s", response.status, endpoint, text
            )
            return None
    except Exception as e:
        logger.exception("Сетевая ошибка при direct запросе к API %s: %s", endpoint, e)
        return None


async def _make_core_request(
    endpoint: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Запрос к API Core Gateway.
    """
    url = f"{settings.CORE_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {"Content-Type": "application/json"}
    if settings.BOT_API_KEY:
        headers["X-Bot-Api-Key"] = settings.BOT_API_KEY

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
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if response.status in (200, 201):
                return await response.json()
            else:
                text = await response.text()
                logger.error(
                    "Ошибка Core API [%d] для %s: %s", response.status, endpoint, text
                )
                return None
    except Exception as e:
        logger.exception("Сетевая ошибка при запросе к Core API %s: %s", endpoint, e)
        return None


# --- Методы для работы через Core API (поведение шлюза, для автоответов и классификатора) ---


async def get_single_task(task_id: int) -> Optional[Dict[str, Any]]:
    return await _make_core_request(endpoint=f"service/tasks/{task_id}", method="GET")


async def add_task_comment(task_id: int, comment: str) -> bool:
    payload = {"comment": comment}
    res = await _make_core_request(
        endpoint=f"service/tasks/{task_id}/comment", method="POST", json_data=payload
    )
    return res is not None


async def update_task_status(task_id: int, status_id: int) -> bool:
    payload = {"status_id": status_id}
    res = await _make_core_request(
        endpoint=f"service/tasks/{task_id}/status", method="POST", json_data=payload
    )
    return res is not None


async def update_task_custom_fields(
    task_id: int, custom_field_values: List[Dict[str, Any]]
) -> bool:
    payload = {"custom_field_values": custom_field_values}
    res = await _make_core_request(
        endpoint=f"service/tasks/{task_id}/custom-fields",
        method="PUT",
        json_data=payload,
    )
    return res is not None


# --- Прямые методы IntraService (для RAG билдера, которому нужны массовые выборки) ---


async def get_tasks(auth_b64: str, filters: Optional[Dict[str, Any]] = None) -> Any:
    params = {"include": "status"}
    if filters:
        params.update(filters)
    return await _make_direct_request(
        endpoint="task", method="GET", auth_b64=auth_b64, params=params
    )


async def get_task_lifetime(
    auth_b64: str, task_id: int
) -> Optional[List[Dict[str, Any]] | Dict[str, Any]]:
    return await _make_direct_request(
        endpoint="tasklifetime",
        method="GET",
        auth_b64=auth_b64,
        params={"taskid": task_id},
    )


async def get_services(auth_b64: str) -> Optional[List[Dict[str, Any]]]:
    return await _make_direct_request(
        endpoint="service",
        method="GET",
        auth_b64=auth_b64,
        params={"include": "parentid", "for": "createtask", "pagesize": "1000"},
    )


async def get_tasks_by_status(
    auth_b64: str, status_id: int, page: int = 1, page_size: int = 100
) -> Any:
    params = {
        "include": "status,customfields,service",
        "StatusIds": str(status_id),
        "page": page,
        "pageSize": page_size,
    }
    return await _make_direct_request(
        endpoint="task", method="GET", auth_b64=auth_b64, params=params
    )


async def get_task_comments(
    auth_b64: str, task_id: int
) -> Optional[List[Dict[str, Any]] | Dict[str, Any]]:
    return await get_task_lifetime(auth_b64, task_id)
