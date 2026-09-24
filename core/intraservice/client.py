"""High-performance Async HTTPX client for IntraService API with Circuit Breaker and Domain Invariants."""

import asyncio
import base64
import enum
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.intraservice.catalog import ServiceCatalog
from core.intraservice.dto import (
    ServiceDTO,
    TaskDTO,
    TaskLifetimeEventDTO,
    TaskStatusDTO,
    TaskTypeDTO,
)
from core.intraservice.exceptions import (
    CircuitBreakerOpenError,
    IntraServiceAuthError,
    IntraServiceError,
    IntraServiceNotFoundError,
    IntraServiceServerError,
    IntraServiceValidationError,
)
from core.intraservice.parser import enrich_task_dict

logger = logging.getLogger("core.intraservice")

DEFAULT_STATUS_MAP: Dict[int, str] = {
    1: "Новая",
    2: "В работе",
    3: "Выполнена",
    4: "Закрыта",
    5: "Отклонена",
    6: "Приостановлена",
    7: "Переоткрыта",
    30: "Отменена",
}


class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Circuit Breaker with exponential backoff protecting from cascading Helpdesk API failures."""

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
        self.last_error: Optional[str] = None

    @property
    def current_cooldown(self) -> float:
        if self.consecutive_trips <= 0:
            return self.base_recovery_timeout
        calculated = self.base_recovery_timeout * (self.backoff_factor ** (self.consecutive_trips - 1))
        return min(calculated, self.max_recovery_timeout)

    def can_execute(self) -> bool:
        now = time.monotonic()
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if now - self.last_state_change >= self.current_cooldown:
                logger.info(
                    "Circuit Breaker entering HALF_OPEN probe mode (after %.1fs cooldown)...",
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
        if self.state != CircuitState.CLOSED:
            logger.info("Circuit Breaker restored to CLOSED state. IntraService API healthy.")
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.consecutive_trips = 0
        self.last_error = None

    def record_failure(self, error: Any = None) -> None:
        now = time.monotonic()
        self.last_error = str(error) if error else "Unknown error"
        self.failure_count += 1

        if self.state == CircuitState.HALF_OPEN:
            self.consecutive_trips += 1
            self.state = CircuitState.OPEN
            self.last_state_change = now
            logger.warning(
                "Circuit Breaker test probe failed (%s). Re-opening for %.1fs (trip #%d).",
                self.last_error,
                self.current_cooldown,
                self.consecutive_trips,
            )
        elif self.state == CircuitState.CLOSED and self.failure_count >= self.failure_threshold:
            self.consecutive_trips = 1
            self.state = CircuitState.OPEN
            self.last_state_change = now
            logger.error(
                "Circuit Breaker TRIPPED (OPEN): %d consecutive failures (%s). Blocked for %.1fs.",
                self.failure_count,
                self.last_error,
                self.current_cooldown,
            )

    def remaining_cooldown(self) -> float:
        if self.state != CircuitState.OPEN:
            return 0.0
        time_in_state = time.monotonic() - self.last_state_change
        return max(0.0, self.current_cooldown - time_in_state)


class IntraServiceClient:
    """Asynchronous client for interacting with IntraService API v4.47."""

    def __init__(
        self,
        base_url: str,
        auth_b64: Optional[str] = None,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ) -> None:
        # INVARIANT (GEMINI.md): Base URL must end with /api, no duplication allowed
        cleaned_url = base_url.rstrip("/")
        if not cleaned_url.endswith("/api"):
            cleaned_url = f"{cleaned_url}/api"
        self.base_url = cleaned_url

        self.auth_b64 = auth_b64
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.circuit_breaker = CircuitBreaker()
        self._client: Optional[httpx.AsyncClient] = None
        self.catalog = ServiceCatalog()

    async def get_http_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                verify=self.verify_ssl,
                timeout=httpx.Timeout(self.timeout),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        endpoint: str,
        auth_b64: Optional[str] = None,
        auth_header: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Any:
        if not self.circuit_breaker.can_execute():
            raise CircuitBreakerOpenError(self.circuit_breaker.remaining_cooldown())

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        token = auth_b64 or self.auth_b64

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Basic {token}"
        elif auth_header:
            headers["Authorization"] = auth_header

        client = await self.get_http_client()

        for attempt in range(max_retries):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                )

                if response.status_code in (200, 201):
                    self.circuit_breaker.record_success()
                    try:
                        return response.json()
                    except Exception:
                        return {}

                if response.status_code == 204:
                    self.circuit_breaker.record_success()
                    return {}

                if response.status_code in (401, 403):
                    raise IntraServiceAuthError(
                        f"Authentication failed ({response.status_code}): {response.text}",
                        status_code=response.status_code,
                    )

                if response.status_code == 404:
                    raise IntraServiceNotFoundError(
                        f"Resource not found: {endpoint}",
                        status_code=404,
                    )

                if response.status_code == 400:
                    raise IntraServiceValidationError(
                        f"Bad request to {endpoint}: {response.text}",
                        status_code=400,
                    )

                # 5xx Server Errors
                if response.status_code >= 500:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                    self.circuit_breaker.record_failure(f"HTTP {response.status_code}: {response.text[:100]}")
                    raise IntraServiceServerError(
                        f"Server error {response.status_code} at {endpoint}",
                        status_code=response.status_code,
                    )

                raise IntraServiceError(
                    f"Unexpected status code {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )

            except (httpx.RequestError, httpx.TimeoutException) as exc:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                self.circuit_breaker.record_failure(exc)
                raise IntraServiceError(f"Network error during {method} {url}: {exc}") from exc

    async def verify_credentials(self, login: str, password: str) -> Tuple[Optional[str], Optional[int]]:
        """Verify username & password against IntraService API."""
        encoded_auth = base64.b64encode(f"{login}:{password}".encode()).decode()
        auth_header = f"Basic {encoded_auth}"
        try:
            data = await self._request(
                method="GET",
                endpoint="user",
                auth_header=auth_header,
                params={"getcurrentuserinfo": "true"},
            )
            if isinstance(data, dict) and "Id" in data:
                return encoded_auth, data["Id"]
        except IntraServiceAuthError:
            return None, None
        return None, None

    async def get_task(self, task_id: int, auth_b64: Optional[str] = None) -> TaskDTO:
        """Fetch full ticket details with custom fields, status and attachments."""
        raw = await self._request(
            method="GET",
            endpoint=f"task/{task_id}",
            auth_b64=auth_b64,
            params={"include": "customfields,status,service,attachments"},
        )
        task_data: Dict[str, Any] = {}
        if isinstance(raw, dict):
            if "Task" in raw and isinstance(raw["Task"], dict):
                task_data = dict(raw["Task"])
            elif "Tasks" in raw and isinstance(raw["Tasks"], list) and raw["Tasks"]:
                task_data = dict(raw["Tasks"][0])
            else:
                task_data = dict(raw)

            # Extract included attachments if top-level
            if "Attachments" in raw and "Attachments" not in task_data:
                task_data["Attachments"] = raw["Attachments"]
            # Extract included status name if not in task_data
            status_id = task_data.get("StatusId")
            if not task_data.get("StatusName") and status_id is not None:
                try:
                    s_id_int = int(status_id)
                    if "Statuses" in raw and isinstance(raw["Statuses"], list):
                        for s in raw["Statuses"]:
                            if isinstance(s, dict) and s.get("Id") == s_id_int:
                                task_data["StatusName"] = s.get("Name", "")
                                break
                    if not task_data.get("StatusName"):
                        task_data["StatusName"] = DEFAULT_STATUS_MAP.get(s_id_int, f"Статус {status_id}")
                except (ValueError, TypeError):
                    pass
            # Extract included service name if not in task_data
            if "Services" in raw and isinstance(raw["Services"], list) and raw["Services"]:
                if not task_data.get("ServiceName") and isinstance(raw["Services"][0], dict):
                    task_data["ServiceName"] = raw["Services"][0].get("Name", "")
            # Extract included priority name if not in task_data
            if "Priorities" in raw and isinstance(raw["Priorities"], list) and raw["Priorities"]:
                if not task_data.get("PriorityName") and isinstance(raw["Priorities"][0], dict):
                    task_data["PriorityName"] = raw["Priorities"][0].get("Name", "")

        # Invariant: Ensure Id is set
        if not task_data.get("Id"):
            task_data["Id"] = task_id

        enriched = enrich_task_dict(task_data)
        return TaskDTO.model_validate(enriched)

    async def get_tasks_by_filter(
        self,
        filter_id: int,
        page: int = 1,
        page_size: int = 200,
        service_ids: Optional[List[int]] = None,
        fetch_all_pages: bool = False,
        max_pages: int = 3,
        auth_b64: Optional[str] = None,
    ) -> List[TaskDTO]:
        """Fetch tickets matching an IntraService filter ID with pagination."""
        base_params: Dict[str, Any] = {
            "filterid": str(filter_id),
            "include": "status,customfields,service",
            "pagesize": str(min(page_size, 2000)),
        }
        if service_ids:
            base_params["ServiceIds"] = ",".join(str(s) for s in service_ids)

        all_tasks: List[TaskDTO] = []
        current_page = page

        while True:
            params = dict(base_params)
            params["page"] = str(current_page)
            res = await self._request(
                method="GET",
                endpoint="task",
                params=params,
                auth_b64=auth_b64,
            )

            raw_tasks = []
            paginator = {}
            status_map = dict(DEFAULT_STATUS_MAP)

            if isinstance(res, dict):
                raw_tasks = res.get("Tasks", []) or []
                paginator = res.get("Paginator", {}) or {}
                if "Statuses" in res and isinstance(res["Statuses"], list):
                    for s in res["Statuses"]:
                        if isinstance(s, dict) and "Id" in s and "Name" in s:
                            try:
                                status_map[int(s["Id"])] = str(s["Name"])
                            except (ValueError, TypeError):
                                pass
            elif isinstance(res, list):
                raw_tasks = res

            for t in raw_tasks:
                if isinstance(t, dict):
                    # Ensure StatusName is populated from StatusId
                    s_id = t.get("StatusId")
                    if not t.get("StatusName") and s_id is not None:
                        try:
                            t["StatusName"] = status_map.get(int(s_id), f"Статус {s_id}")
                        except (ValueError, TypeError):
                            pass
                    enriched = enrich_task_dict(t)
                    all_tasks.append(TaskDTO.model_validate(enriched))

            if not fetch_all_pages:
                break

            total_pages = int(paginator.get("PageCount") or 1)
            if current_page >= total_pages or current_page >= (page + max_pages - 1) or not raw_tasks:
                break

            current_page += 1

        return all_tasks

    async def get_task_lifetime(self, task_id: int, auth_b64: Optional[str] = None) -> List[TaskLifetimeEventDTO]:
        """Get ticket change history and comments."""
        res = await self._request(
            method="GET",
            endpoint="tasklifetime",
            params={"taskid": str(task_id), "include": "status"},
            auth_b64=auth_b64,
        )
        raw_events = []
        status_map: Dict[int, str] = {}
        if isinstance(res, dict):
            if "Statuses" in res and isinstance(res["Statuses"], list):
                for s in res["Statuses"]:
                    if isinstance(s, dict) and "Id" in s and "Name" in s:
                        status_map[int(s["Id"])] = str(s["Name"])

            if "TaskLifetimes" in res and isinstance(res["TaskLifetimes"], list):
                raw_events = res["TaskLifetimes"]
            elif "Lifetimes" in res and isinstance(res["Lifetimes"], list):
                raw_events = res["Lifetimes"]
        elif isinstance(res, list):
            raw_events = res

        events: List[TaskLifetimeEventDTO] = []
        for idx, e in enumerate(raw_events):
            if isinstance(e, dict):
                e_copy = dict(e)
                if not e_copy.get("Id"):
                    e_copy["Id"] = idx + 1
                if not e_copy.get("TaskId"):
                    e_copy["TaskId"] = task_id
                if not e_copy.get("Created") and e_copy.get("Date"):
                    e_copy["Created"] = e_copy.get("Date")
                if not e_copy.get("UserName") and e_copy.get("Editor"):
                    e_copy["UserName"] = e_copy.get("Editor")
                if not e_copy.get("NewStatusName") and e_copy.get("StatusId"):
                    try:
                        sid = int(e_copy["StatusId"])
                        if sid in status_map:
                            e_copy["NewStatusName"] = status_map[sid]
                    except (ValueError, TypeError):
                        pass

                events.append(TaskLifetimeEventDTO.model_validate(e_copy))

        return events

    async def get_services(self, auth_b64: Optional[str] = None) -> List[ServiceDTO]:
        """Fetch all services from catalog (GET only as per GEMINI.md)."""
        res = await self._request(
            method="GET",
            endpoint="service",
            params={"include": "parentid", "for": "createtask", "pagesize": "1000"},
            auth_b64=auth_b64,
        )
        raw_services = []
        if isinstance(res, dict):
            raw_services = res.get("Services", []) or []
        elif isinstance(res, list):
            raw_services = res

        services = [ServiceDTO.model_validate(s) for s in raw_services if isinstance(s, dict)]
        self.catalog.load(services)
        return services

    async def get_statuses(self, auth_b64: Optional[str] = None) -> List[TaskStatusDTO]:
        """Fetch ticket status dictionary."""
        res = await self._request(
            method="GET",
            endpoint="taskstatus",
            auth_b64=auth_b64,
        )
        raw_statuses = []
        if isinstance(res, dict) and "TaskStatuses" in res:
            raw_statuses = res["TaskStatuses"]
        elif isinstance(res, list):
            raw_statuses = res

        return [TaskStatusDTO.model_validate(s) for s in raw_statuses if isinstance(s, dict)]

    async def get_task_type(self, task_type_id: int, service_id: int, auth_b64: Optional[str] = None) -> TaskTypeDTO:
        """Fetch task type details.

        INVARIANT (GEMINI.md): serviceid is strictly required by IntraService API.
        """
        res = await self._request(
            method="GET",
            endpoint=f"tasktype/{task_type_id}",
            params={"serviceid": service_id},
            auth_b64=auth_b64,
        )
        res["ServiceId"] = service_id
        return TaskTypeDTO.model_validate(res)

    async def update_task(
        self,
        task_id: int,
        status_id: Optional[int] = None,
        comment: Optional[str] = None,
        executor_ids: Optional[str] = None,
        is_private: bool = False,
        auth_b64: Optional[str] = None,
    ) -> bool:
        """Atomically update ticket status, comment, or assignees."""
        payload: Dict[str, Any] = {"Id": task_id}
        if status_id is not None:
            payload["StatusId"] = status_id
        if comment:
            payload["Comment"] = comment
            payload["IsPrivateComment"] = is_private
        if executor_ids:
            payload["ExecutorIds"] = str(executor_ids)

        try:
            await self._request(
                method="PUT",
                endpoint=f"task/{task_id}",
                json_data=payload,
                auth_b64=auth_b64,
            )
            return True
        except IntraServiceValidationError:
            # Fallback if executor_ids rejected by role permissions
            if executor_ids and (status_id is not None or comment is not None):
                fb_payload = {"Id": task_id}
                if status_id is not None:
                    fb_payload["StatusId"] = status_id
                if comment:
                    fb_payload["Comment"] = comment
                    fb_payload["IsPrivateComment"] = is_private
                await self._request(
                    method="PUT",
                    endpoint=f"task/{task_id}",
                    json_data=fb_payload,
                    auth_b64=auth_b64,
                )
                return True
            raise

    async def add_task_comment(
        self,
        task_id: int,
        comment: str,
        is_private: bool = False,
        auth_b64: Optional[str] = None,
    ) -> bool:
        """Add a comment to an IntraService ticket."""
        return await self.update_task(
            task_id=task_id,
            comment=comment,
            is_private=is_private,
            auth_b64=auth_b64,
        )

    async def download_attachment(self, file_id: int, auth_b64: Optional[str] = None) -> Optional[bytes]:
        """Download binary attachment from IntraService."""
        url = f"{self.base_url}/taskfile/{file_id}"
        token = auth_b64 or self.auth_b64
        headers = {}
        if token:
            headers["Authorization"] = f"Basic {token}"

        client = await self.get_http_client()
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            return response.content
        return None
