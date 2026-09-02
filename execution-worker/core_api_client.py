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
BOT_API_KEY = os.getenv("BOT_API_KEY", "dev_bot_api_key_tempo_2026")


class CoreApiClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_sec: float = 15.0,
    ):
        self.base_url = (base_url or CORE_API_URL).rstrip("/")
        self.api_key = api_key or BOT_API_KEY
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "X-Bot-Api-Key": self.api_key,
                "Content-Type": "application/json",
            }
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
    ) -> bool:
        """Публикует отчетный комментарий и обновляет статус тикета через Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/apply"
        payload = {
            "task_id": task_id,
            "comment": comment,
            "status_id": status_id,
            "expenses": expenses,
            "is_service": is_service,
        }
        try:
            async with session.post(url, json=payload) as resp:
                return resp.status == 200
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
