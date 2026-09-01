"""
Клиент Core API Gateway для Helpdesk Agent CLI.
Все данные (заявки, каталог, RAG, правила, списание) запрашиваются исключительно через Core API.
"""

import logging
import os
from typing import Any
import aiohttp

logger = logging.getLogger("helpdesk_agent.core_client")

CORE_API_URL = os.getenv("CORE_API_URL", "http://127.0.0.1:8000").rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "dev_bot_api_key_tempo_2026")


class CoreApiClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_sec: float = 30.0,
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
            self._session = aiohttp.ClientSession(
                headers=headers, timeout=self.timeout
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_triage_batch(
        self,
        filter_id: int = 984,
        limit: int = 5,
        page: int = 1,
        service_prefix: str | None = None,
        redirect_only: bool = False,
        include_skipped: bool = False,
    ) -> dict[str, Any]:
        """Получает подготовленную пачку заявок с авто-рекомендациями от Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/batch"
        params: dict[str, Any] = {
            "filter_id": filter_id,
            "limit": limit,
            "page": page,
            "redirect_only": str(redirect_only).lower(),
            "include_skipped": str(include_skipped).lower(),
        }
        if service_prefix:
            params["service_prefix"] = service_prefix

        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            err_text = await resp.text()
            logger.error(
                "Ошибка get_triage_batch (HTTP %d): %s", resp.status, err_text
            )
            return {
                "total_open": 0,
                "filter_id": filter_id,
                "page": page,
                "tasks": [],
                "duplicates": [],
            }

    async def get_task_details(self, task_id: int) -> dict[str, Any] | None:
        """Получает детальную карточку задачи от Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/tasks/{task_id}"
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("task")
            logger.error(
                "Ошибка get_task_details #%d: HTTP %d", task_id, resp.status
            )
            return None

    async def get_task_card(self, task_id: int) -> dict[str, Any] | None:
        """Получает полную карточку (задача + история + RAG + рекомендация)."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/tasks/{task_id}"
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            return None

    async def get_task_history(self, task_id: int) -> list[dict[str, Any]]:
        """Получает историю изменений (lifetime) задачи."""
        card = await self.get_task_card(task_id)
        return card.get("history", []) if card else []

    async def apply_decision(
        self,
        task_ids: list[int],
        status_id: int,
        comment: str = "",
        expenses: int = 0,
        executor_ids: str = "8664,10502",
        dry_run: bool = False,
    ) -> bool:
        """Атомарно применяет решение к списку задач через Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/apply"
        payload = {
            "task_ids": task_ids,
            "status_id": status_id,
            "comment": comment,
            "expenses": expenses,
            "executor_ids": executor_ids,
            "dry_run": dry_run,
        }
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                return all(r.get("status") == "success" for r in results)
            err_text = await resp.text()
            logger.error(
                "Ошибка apply_decision (HTTP %d): %s", resp.status, err_text
            )
            return False

    async def get_duplicates(
        self, filter_id: int = 984, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Получает список обнаруженных дубликатов."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/duplicates"
        params = {"filter_id": filter_id, "limit": limit}
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("duplicates", [])
            return []

    async def search_kb(
        self, query: str, limit: int = 3, threshold: float = 0.70
    ) -> list[dict[str, Any]]:
        """Семантический поиск в базе знаний pgvector через Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/rag/search"
        payload = {"query": query, "limit": limit, "threshold": threshold}
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("matches", [])
            return []

    async def index_kb(
        self,
        task_id: int,
        original_name: str,
        problem: str,
        solution: str,
        service_id: int,
        service_name: str,
        status_name: str,
        classification_data: dict[str, Any] | None = None,
    ) -> bool:
        """Прямая индексация решения задачи в Core API RAG."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/rag/index"
        payload = {
            "task_id": task_id,
            "original_name": original_name,
            "problem": problem,
            "solution": solution,
            "service_id": service_id,
            "service_name": service_name,
            "status_name": status_name,
            "classification_data": classification_data or {},
        }
        async with session.post(url, json=payload) as resp:
            return resp.status == 200

    async def sync_kb(self, days: int = 30, limit: int = 50) -> dict[str, Any]:
        """Запускает синхронизацию закрытых заявок в RAG через Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/rag/sync"
        payload = {"days": days, "limit": limit}
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                return await resp.json()
            return {"status": "error", "http_status": resp.status}

    async def get_services(self) -> list[dict[str, Any]]:
        """Получает список корневых сервисов 01..16."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/services"
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            return []

    async def get_catalog(
        self, search: str | None = None
    ) -> list[dict[str, Any]]:
        """Получает полный каталог услуг."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/catalog"
        params = {"search": search} if search else {}
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            return []

    async def skip_tasks(
        self, task_ids: list[int], reason: str = "operator_skipped"
    ) -> bool:
        """Помечает задачи как пропущенные в Redis через Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/session/skip"
        payload = {"task_ids": task_ids, "reason": reason}
        async with session.post(url, json=payload) as resp:
            return resp.status == 200

    async def reset_session(self) -> bool:
        """Сбрасывает сессию пропущенных задач в Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/session/reset"
        async with session.post(url) as resp:
            return resp.status == 200
