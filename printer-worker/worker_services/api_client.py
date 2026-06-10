import aiohttp
import logging
from typing import Optional, Dict, Any, List
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
    json_data: Optional[Dict[str, Any]] = None
) -> Optional[Any]:
    url = f"{CORE_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"
    headers = {
        "Content-Type": "application/json",
        "X-Bot-Api-Key": BOT_API_KEY
    }

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
            timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            if response.status in (200, 201):
                return await response.json()
            else:
                text = await response.text()
                logger.error("Ошибка Core API [%d] для %s: %s", response.status, endpoint, text)
                return None
    except Exception as e:
        logger.exception("Сетевая ошибка при запросе к Core API %s: %s", endpoint, e)
        return None

async def get_task_details(tg_user_id: int, task_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает детальную информацию по конкретной задаче включая кастомные поля.
    Использует GET /tasks/{task_id} — специализированный эндпоинт с include=customfields.
    """
    params = {"tg_user_id": tg_user_id}
    return await _make_request(
        endpoint=f"tasks/{task_id}",
        method="GET",
        params=params
    )

async def add_task_comment(tg_user_id: int, task_id: int, comment: str) -> bool:
    """
    Добавляет комментарий в задачу через Core API.
    Поскольку Core API роутер для изменения задач не всегда содержит отдельный endpoint,
    мы можем сделать заглушку-запрос или использовать post-запрос, если Core API его поддерживает.
    Если Core API не имеет прямого эндпоинта для комментирования, логируем это действие.
    В реальной системе это проксируется в IntraService.
    """
    logger.info("Добавление комментария в задачу #%d для пользователя %s: %s", task_id, tg_user_id, comment)
    # Предполагаем наличие POST-запроса или прокси
    payload = {
        "tg_user_id": tg_user_id,
        "comment": comment
    }
    res = await _make_request(
        endpoint=f"tasks/{task_id}/comment",
        method="POST",
        json_data=payload
    )
    return res is not None

async def update_task_status(tg_user_id: int, task_id: int, status_id: int) -> bool:
    """
    Обновляет статус задачи.
    """
    logger.info("Обновление статуса задачи #%d на статус_id %d для пользователя %s", task_id, status_id, tg_user_id)
    payload = {
        "tg_user_id": tg_user_id,
        "status_id": status_id
    }
    res = await _make_request(
        endpoint=f"tasks/{task_id}/status",
        method="POST",
        json_data=payload
    )
    return res is not None


async def add_task_expenses(tg_user_id: int, task_id: int, minutes: int) -> bool:
    """
    Добавляет трудозатраты в задачу через Core API.
    """
    logger.info("Добавление трудозатрат в задачу #%d (%d мин) для пользователя %s", task_id, minutes, tg_user_id)
    payload = {
        "tg_user_id": tg_user_id,
        "minutes": minutes
    }
    res = await _make_request(
        endpoint=f"tasks/{task_id}/expenses",
        method="POST",
        json_data=payload
    )
    return res is not None

