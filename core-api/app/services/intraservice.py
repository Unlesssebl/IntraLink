import asyncio
import base64
import enum
import logging
import time
from datetime import datetime
from http import HTTPStatus
from typing import Any

import aiohttp

from app.config import settings
from app.services.crypto import decrypt_token
from app.utils.date_utils import parse_api_date
from app.utils.intraservice_parser import enrich_task_data

logger = logging.getLogger(__name__)


class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    Автоматический предохранитель (Circuit Breaker) с экспоненциальным backoff
    для защиты от перегрузки и зависания при недоступности корпоративного API IntraService.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        base_recovery_timeout: float = 10.0,
        max_recovery_timeout: float = 300.0,
        backoff_factor: float = 2.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.base_recovery_timeout = base_recovery_timeout
        self.max_recovery_timeout = max_recovery_timeout
        self.backoff_factor = backoff_factor

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.consecutive_trips = 0
        self.last_state_change = 0.0
        self.last_failure_time = 0.0
        self.last_error: str | None = None

    @property
    def current_cooldown(self) -> float:
        """Текущий таймаут охлаждения с учетом экспоненциального backoff."""
        if self.consecutive_trips <= 0:
            return self.base_recovery_timeout
        calculated = self.base_recovery_timeout * (
            self.backoff_factor ** (self.consecutive_trips - 1)
        )
        return min(calculated, self.max_recovery_timeout)

    def can_execute(self) -> bool:
        """
        Проверяет, разрешено ли выполнение запроса.
        - CLOSED: разрешено
        - OPEN: проверяет, истек ли таймаут охлаждения. Если истек — переходит в HALF_OPEN.
        - HALF_OPEN: разрешает тестовый запрос
        """
        now = time.monotonic()
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if now - self.last_state_change >= self.current_cooldown:
                logger.info(
                    "Circuit Breaker переходит в HALF_OPEN (проверочный запрос после %.1f с охлаждения)...",
                    self.current_cooldown,
                )
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = now
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return True

        return False

    def record_success(self) -> None:
        """
        Фиксирует успешный ответ от сервера.
        Сбрасывает счетчики сбоев и возвращает состояние в CLOSED.
        """
        if self.state != CircuitState.CLOSED:
            logger.info(
                "Circuit Breaker восстановил состояние CLOSED (IntraService API снова доступен)."
            )
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.consecutive_trips = 0
        self.last_error = None

    def record_failure(self, error: str | Exception | None = None) -> None:
        """
        Фиксирует сетевой сбой или серверную ошибку (5xx).
        При превышении порога переводит предохранитель в состояние OPEN.
        """
        now = time.monotonic()
        self.last_failure_time = now
        self.last_error = str(error) if error else "Unknown error"
        self.failure_count += 1

        if self.state == CircuitState.HALF_OPEN:
            self.consecutive_trips += 1
            self.state = CircuitState.OPEN
            self.last_state_change = now
            logger.warning(
                "Circuit Breaker: тестовый запрос в HALF_OPEN не удался (%s). Возврат в OPEN на %.1f с (трип #%d).",
                self.last_error,
                self.current_cooldown,
                self.consecutive_trips,
            )
        elif (
            self.state == CircuitState.CLOSED
            and self.failure_count >= self.failure_threshold
        ):
            self.consecutive_trips = 1
            self.state = CircuitState.OPEN
            self.last_state_change = now
            logger.error(
                "Circuit Breaker СРАБОТАЛ (OPEN): %d последовательных сбоев IntraService API (%s). Запросы заблокированы на %.1f с.",
                self.failure_count,
                self.last_error,
                self.current_cooldown,
            )

    def reset(self) -> None:
        """Сброс состояния Circuit Breaker."""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.consecutive_trips = 0
        self.last_state_change = 0.0
        self.last_failure_time = 0.0
        self.last_error = None

    def get_status(self) -> dict[str, Any]:
        """Возвращает текущие метрики Circuit Breaker."""
        now = time.monotonic()
        time_in_state = (
            now - self.last_state_change if self.last_state_change > 0 else 0.0
        )
        remaining_cooldown = (
            max(0.0, self.current_cooldown - time_in_state)
            if self.state == CircuitState.OPEN
            else 0.0
        )
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "consecutive_trips": self.consecutive_trips,
            "current_cooldown_seconds": self.current_cooldown,
            "remaining_cooldown_seconds": remaining_cooldown,
            "last_error": self.last_error,
        }


circuit_breaker = CircuitBreaker()


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
    max_retries: int = 3,
) -> Any | None:
    """
    Универсальная функция для выполнения HTTP-запросов к API IntraService
    с поддержкой Circuit Breaker и автоматического Exponential Backoff.
    """
    if not circuit_breaker.can_execute():
        status = circuit_breaker.get_status()
        logger.warning(
            "Circuit Breaker [OPEN]: запрос к %s отклонен без выполнения (осталось охлаждения: %.1f с)",
            endpoint,
            status["remaining_cooldown_seconds"],
        )
        return None

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

    for attempt in range(max_retries):
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
                    circuit_breaker.record_success()
                    try:
                        return await response.json()
                    except Exception:
                        return {}
                elif response.status == HTTPStatus.NO_CONTENT:
                    circuit_breaker.record_success()
                    return {}

                # 5xx ошибки сервера IntraService (инфраструктурный сбой)
                if response.status >= 500:
                    text = await response.text()
                    logger.error(
                        "Ошибка сервера IntraService [%d] для %s (попытка %d/%d): %s",
                        response.status,
                        endpoint,
                        attempt + 1,
                        max_retries,
                        text,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    circuit_breaker.record_failure(
                        f"HTTP {response.status}: {text[:100]}"
                    )
                    return None

                # 4xx клиентские ошибки (неверные учетные данные, 404 и т.д.)
                text = await response.text()
                logger.error("Ошибка API [%d] для %s: %s", response.status, endpoint, text)
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(
                "Сетевой сбой при запросе к API %s (попытка %d/%d): %s",
                endpoint,
                attempt + 1,
                max_retries,
                e,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            circuit_breaker.record_failure(e)
            return None
        except Exception as e:
            logger.exception("Непредвиденная ошибка при запросе к API %s: %s", endpoint, e)
            circuit_breaker.record_failure(e)
            return None

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
    Нормализует ответ: если API возвращает словарь вида {"TaskLifetimes": [...]},
    извлекает и возвращает список событий.
    """
    res = await _make_request(
        endpoint="tasklifetime",
        method="GET",
        auth_b64=auth_b64,
        params={"taskid": task_id},
    )
    if isinstance(res, dict) and "TaskLifetimes" in res:
        return res["TaskLifetimes"]
    if isinstance(res, list):
        return res
    return [] if res is not None else None


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


async def get_single_task(
    auth_b64: str, task_id: int, include_rights: bool = True
) -> dict[str, Any] | None:
    """
    Получает детальную информацию по конкретной задаче с кастомными полями, правами и метаданными.
    """
    include_str = "customfields,status,service,comments,attachments"
    if include_rights:
        include_str += ",usertaskrights"

    res = await _make_request(
        endpoint=f"task/{task_id}",
        method="GET",
        auth_b64=auth_b64,
        params={"include": include_str},
    )
    if res:
        return enrich_task_data(res)
    return None



async def get_tasks_by_filter(
    auth_b64: str,
    filter_id: int,
    page: int = 1,
    page_size: int = 50,
    service_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """
    Получает список задач по ID фильтра очереди IntraService с обогащением кастомными полями.
    """
    params = {
        "filterid": str(filter_id),
        "include": "status,customfields,service,comments,attachments",
        "pagesize": str(page_size),
        "page": str(page),
    }
    if service_ids:
        params["serviceids"] = ",".join(str(s) for s in service_ids)

    res = await _make_request(
        endpoint="task", method="GET", auth_b64=auth_b64, params=params
    )
    tasks = []
    if isinstance(res, dict):
        tasks = res.get("Tasks", [])
    elif isinstance(res, list):
        tasks = res

    return [enrich_task_data(t) for t in tasks if t and isinstance(t, dict)]


async def download_attachment_file(
    auth_b64: str, task_id: int, file_id: int
) -> bytes | None:
    """
    Скачивает бинарное содержимое прикрепленного файла задачи из IntraService.
    """
    global _session
    if _session is None or _session.closed:
        await init_session()
    assert _session is not None

    decrypted_auth = decrypt_token(auth_b64)
    headers = {"Authorization": f"Basic {decrypted_auth}"}
    url = f"{settings.INTRASERVICE_URL.rstrip('/')}/api/task/{task_id}/attachment/{file_id}"

    try:
        async with _session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30.0)) as response:
            if response.status == 200:
                return await response.read()
            logger.warning(
                "Ошибка скачивания вложения #%d для задачи #%d: HTTP %d",
                file_id,
                task_id,
                response.status,
            )
            return None
    except Exception as e:
        logger.exception(
            "Исключение при скачивании вложения #%d для задачи #%d: %s",
            file_id,
            task_id,
            e,
        )
        return None


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


async def add_task_expenses(auth_b64: str, task_id: int, minutes: int, user_id: int | None = None) -> bool:
    """
    Добавляет трудозатраты к задаче в IntraService.
    """
    payload: dict[str, Any] = {"TaskId": task_id, "Minutes": minutes}
    if user_id is not None:
        payload["UserId"] = user_id
    res = await _make_request(
        endpoint="taskexpenses",
        method="POST",
        auth_b64=auth_b64,
        json_data=payload,
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
    res = await _make_request(
        endpoint="task", method="GET", auth_b64=auth_b64, params=params
    )
    if isinstance(res, list):
        return [enrich_task_data(t) for t in res if t and isinstance(t, dict)]
    elif isinstance(res, dict) and "Tasks" in res:
        res["Tasks"] = [enrich_task_data(t) for t in res["Tasks"] if t and isinstance(t, dict)]
    return res


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


async def update_task_full(
    auth_b64: str,
    task_id: int,
    status_id: int | None = None,
    comment: str | None = None,
    executor_ids: str | None = None,
    is_private: bool = False,
) -> bool:
    """
    Атомарно обновляет задачу (статус, комментарий, исполнители) в одном PUT запросе.
    Если передача исполнителей блокируется правами роли (400), безопасно выполняет
    обновление статуса и комментария без ExecutorIds.
    """
    payload: dict[str, Any] = {"Id": task_id}
    if status_id is not None:
        payload["StatusId"] = status_id
    if comment:
        payload["Comment"] = comment
        payload["IsPrivateComment"] = is_private
    if executor_ids:
        payload["ExecutorIds"] = str(executor_ids)
    res = await _make_request(
        endpoint=f"task/{task_id}",
        method="PUT",
        auth_b64=auth_b64,
        json_data=payload,
    )
    if res is not None:
        return True

    # Если запрос с ExecutorIds отклонен (например, ограничение роли IntraService),
    # пробуем применить статус и комментарий без ExecutorIds, чтобы не потерять решение инженера
    if executor_ids and (status_id is not None or comment is not None):
        fallback_payload: dict[str, Any] = {"Id": task_id}
        if status_id is not None:
            fallback_payload["StatusId"] = status_id
        if comment:
            fallback_payload["Comment"] = comment
            fallback_payload["IsPrivateComment"] = is_private
        fallback_res = await _make_request(
            endpoint=f"task/{task_id}",
            method="PUT",
            auth_b64=auth_b64,
            json_data=fallback_payload,
        )
        if fallback_res is not None:
            logger.warning(
                "Задача #%d обновлена без назначения исполнителя (ограничение прав IntraService)",
                task_id,
            )
            return True

    return False

