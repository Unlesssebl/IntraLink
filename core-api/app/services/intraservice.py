from datetime import datetime
import aiohttp
import base64
import logging
from typing import Optional, Dict, Any, List, Tuple
from app.config import settings
from app.services.crypto import decrypt_token

logger = logging.getLogger(__name__)

def parse_api_date(date_str: Optional[str]) -> Optional[datetime]:
    """
    Парсит дату из API IntraService. 
    Поддерживает форматы: YYYY-MM-DD HH:MM:SS, YYYY-MM-DDTHH:MM:SS, DD.MM.YYYY HH:MM:SS
    """
    if not date_str:
        return None
    
    date_str = date_str.replace("T", " ")
    
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    
    logger.warning("Не удалось спарсить дату: %s", date_str)
    return None

_session: Optional[aiohttp.ClientSession] = None

async def init_session() -> None:
    """
    Инициализирует глобальную сессию aiohttp.ClientSession.
    """
    global _session
    if _session is None or _session.closed:
        # TODO(security): Проверить SSL_VERIFY в соответствии с правилами безопасной разработки.
        # Внутренние домены могут требовать отключения проверки SSL в тестовой среде.
        connector = aiohttp.TCPConnector(ssl=settings.SSL_VERIFY)
        _session = aiohttp.ClientSession(connector=connector)

async def close_session() -> None:
    """
    Закрывает глобальную сессию aiohttp.ClientSession.
    """
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None

async def _make_request(
    endpoint: str,
    method: str = "GET",
    auth_b64: Optional[str] = None,
    auth_header: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None
) -> Optional[Any]:
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
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            if response.status == 200:
                return await response.json()
            else:
                text = await response.text()
                logger.error("Ошибка API [%d] для %s: %s", response.status, endpoint, text)
                return None
    except Exception as e:
        logger.exception("Сетевая ошибка при запросе к API %s: %s", endpoint, e)
        return None

async def verify_credentials(login: str, password: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Проверяет учетные данные пользователя в IntraService.
    Возвращает (auth_b64, user_id) при успехе, иначе (None, None).
    """
    auth_header = aiohttp.BasicAuth(login, password).encode()
    
    response_data = await _make_request(
        endpoint="user",
        method="GET",
        auth_header=auth_header,
        params={"getcurrentuserinfo": "true"}
    )
    
    if response_data is not None:
        auth_str = f"{login}:{password}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        user_id = response_data.get("Id")
        return auth_b64, user_id
    return None, None

async def get_tasks(auth_b64: str, filters: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    """
    Получает список задач из IntraService.
    """
    params = {"include": "status"}
    if filters:
        params.update(filters)
        
    return await _make_request(
        endpoint="task",
        method="GET",
        auth_b64=auth_b64,
        params=params
    )

async def get_task_lifetime(auth_b64: str, task_id: int) -> Optional[List[Dict[str, Any]]]:
    """
    Получает историю изменений (lifetime) задачи.
    """
    return await _make_request(
        endpoint="tasklifetime",
        method="GET",
        auth_b64=auth_b64,
        params={"taskid": task_id}
    )

async def get_statuses(auth_b64: str) -> Optional[List[Dict[str, Any]]]:
    """
    Получает справочник статусов задач.
    """
    return await _make_request(
        endpoint="taskstatus",
        method="GET",
        auth_b64=auth_b64
    )
