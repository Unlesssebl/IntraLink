"""
Легковесный клиент Core API Gateway для Windows Execution Worker.
Используется только для получения контекста тикетов и публикации комментариев/результатов.
"""

import logging
import os
from typing import Any
import aiohttp

logger = logging.getLogger("execution_worker.core_client")

CORE_API_URL = os.getenv("CORE_API_URL", "http://127.0.0.1:8000").rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "")
WORKER_API_KEY = os.getenv("WORKER_API_KEY", "")
SERVICE_KEY_ID = os.getenv("WINDOWS_WORKER_SERVICE_KEY_ID") or os.getenv("SERVICE_KEY_ID", "")
SERVICE_SECRET = os.getenv("WINDOWS_WORKER_SERVICE_SECRET") or os.getenv("SERVICE_SECRET", "")


class CoreApiClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_sec: float = 15.0,
    ):
        self.base_url = (base_url or CORE_API_URL).rstrip("/")
        self.api_key = api_key or BOT_API_KEY
        if not ((SERVICE_KEY_ID and SERVICE_SECRET) or self.api_key):
            raise RuntimeError("SERVICE_KEY_ID and SERVICE_SECRET are required for the execution worker")
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {"Content-Type": "application/json"}
            if SERVICE_KEY_ID and SERVICE_SECRET:
                headers.update({"X-Service-Key-Id": SERVICE_KEY_ID, "X-Service-Secret": SERVICE_SECRET})
            else:
                headers.update({
                    "X-Bot-Api-Key": self.api_key,
                    "X-Worker-Api-Key": WORKER_API_KEY or self.api_key,
                })
            connector = aiohttp.TCPConnector(
                limit=15, ttl_dns_cache=300, keepalive_timeout=30.0
            )
            self._session = aiohttp.ClientSession(
                headers=headers, timeout=self.timeout, connector=connector
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_task_details(self, task_id: int) -> dict[str, Any] | None:
        """Получает детализированную карточку задачи из Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/tasks/{task_id}"
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("task")
                logger.debug("Ошибка get_task_details #%d (HTTP %d)", task_id, resp.status)
                return None
        except Exception as e:
            logger.debug("Сбой запроса get_task_details: %s", e)
            return None

    async def add_comment(
        self,
        task_id: int,
        comment: str,
        status_id: int | None = None,
        expenses: int | None = None,
        is_service: bool = True,
        verified_execution_job_id: str | None = None,
    ) -> bool:
        """Публикует отчетный комментарий и обновляет статус тикета через Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/apply"
        payload = {
            "task_ids": [task_id],
            "comment": comment,
            "status_id": status_id or 29,
            "expenses": expenses or 0,
            "confirmed_by_human": True,
            "verified_execution_job_id": verified_execution_job_id,
        }
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results") or []
                    return any(r.get("update_ok", False) for r in results)
                return False
        except Exception as e:
            logger.debug("Сбой добавления комментария к #%d: %s", task_id, e)
            return False

    async def report_command_result(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Отправляет финальный результат выполнения задачи в Command Bus."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/commands/{job_id}/result"
        payload = {
            "status": status,
            "result": result or {},
            "error_message": error_message,
        }
        try:
            async with session.post(url, json=payload) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug("Сбой отправки результата команды %s: %s", job_id, e)
            return False

    async def claim_command_v2(
        self,
        command_id: str,
        worker_id: str,
        message_id: str,
        outbox_id: str | None = None,
        lease_seconds: int = 300,
    ) -> tuple[int, dict[str, Any] | None]:
        """Atomically lease a queued v2 command from the authoritative API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v2/commands/{command_id}/claim"
        try:
            async with session.post(
                url,
                json={
                    "worker_id": worker_id,
                    "message_id": message_id,
                    "outbox_id": outbox_id,
                    "lease_seconds": lease_seconds,
                },
            ) as resp:
                data = await resp.json() if resp.content_type == "application/json" else None
                return resp.status, data
        except Exception as e:
            logger.debug("Сбой lease команды %s: %s", command_id, e)
            return 0, None

    async def finish_command_v2(
        self,
        command_id: str,
        worker_id: str,
        claim_token: str,
        outcome: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Persist a v2 result before the Redis message is acknowledged."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v2/commands/{command_id}/finish"
        try:
            async with session.post(
                url,
                json={
                    "worker_id": worker_id,
                    "claim_token": claim_token,
                    "outcome": outcome,
                    "result": result or {},
                    "error_message": error_message,
                },
            ) as resp:
                return resp.status == 200
        except Exception as e:
            logger.debug("Сбой фиксации результата команды %s: %s", command_id, e)
            return False
