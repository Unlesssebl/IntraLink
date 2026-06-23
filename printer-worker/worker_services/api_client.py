import aiohttp
import logging
from typing import Optional, Dict, Any
from worker_config import CORE_API_URL, BOT_API_KEY

logger = logging.getLogger(__name__)

_session: Optional[aiohttp.ClientSession] = None


async def init_session() -> None:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()


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


async def _make_request(
    endpoint: str,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    url = f"{CORE_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {"Content-Type": "application/json", "X-Bot-Api-Key": BOT_API_KEY}

    if _session is None or _session.closed:
        await init_session()

    assert _session is not None

    import asyncio
    import aiohttp

    for attempt in range(1, 4):
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
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt == 3:
                logger.exception("Сетевая ошибка при запросе к Core API %s после 3 попыток:", endpoint)
                return None
            logger.warning("Сетевая ошибка к %s, попытка %d/3. Ожидание 2с...", endpoint, attempt)
            await asyncio.sleep(2)


async def get_task_details(
    tg_user_id: Optional[int], task_id: int
) -> Optional[Dict[str, Any]]:
    """
    Получает детальную информацию по конкретной задаче включая кастомные поля
    через сервисный эндпоинт Core API.
    """
    return await _make_request(endpoint=f"service/tasks/{task_id}", method="GET")


async def add_task_comment(
    tg_user_id: Optional[int], task_id: int, comment: str
) -> bool:
    """
    Добавляет комментарий в задачу через сервисный эндпоинт Core API.
    """
    logger.info(
        "Добавление комментария в задачу #%d от имени сервисного аккаунта: %s",
        task_id,
        comment,
    )
    payload = {"comment": comment}
    res = await _make_request(
        endpoint=f"service/tasks/{task_id}/comment", method="POST", json_data=payload
    )
    return res is not None


async def update_task_status(
    tg_user_id: Optional[int], task_id: int, status_id: int
) -> bool:
    """
    Обновляет статус задачи через сервисный эндпоинт Core API.
    """
    logger.info(
        "Обновление статуса задачи #%d на статус_id %d от имени сервисного аккаунта",
        task_id,
        status_id,
    )
    payload = {"status_id": status_id}
    res = await _make_request(
        endpoint=f"service/tasks/{task_id}/status", method="POST", json_data=payload
    )
    return res is not None


async def add_task_expenses(
    tg_user_id: Optional[int], task_id: int, minutes: int
) -> bool:
    """
    Добавляет трудозатраты в задачу через сервисный эндпоинт Core API.
    """
    logger.info(
        "Добавление трудозатрат в задачу #%d (%d мин) от имени сервисного аккаунта",
        task_id,
        minutes,
    )
    payload = {"minutes": minutes}
    res = await _make_request(
        endpoint=f"service/tasks/{task_id}/expenses", method="POST", json_data=payload
    )
    return res is not None


async def update_task_custom_fields(
    tg_user_id: Optional[int], task_id: int, custom_field_values: list
) -> bool:
    """
    Обновляет кастомные поля задачи через сервисный эндпоинт Core API.
    """
    logger.info(
        "Обновление кастомных полей задачи #%d от имени сервисного аккаунта: %s",
        task_id,
        custom_field_values,
    )
    payload = {"custom_field_values": custom_field_values}
    res = await _make_request(
        endpoint=f"service/tasks/{task_id}/custom-fields",
        method="PUT",
        json_data=payload,
    )
    return res is not None
