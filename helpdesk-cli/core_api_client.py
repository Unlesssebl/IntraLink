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
        self,
        task_ids: list[int],
        reason: str = "operator_skipped",
        operator_id: str | None = None,
    ) -> bool:
        """Помечает задачи как пропущенные в Redis через Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/session/skip"
        payload = {
            "task_ids": task_ids,
            "reason": reason,
            "operator_id": operator_id,
        }
        async with session.post(url, json=payload) as resp:
            return resp.status == 200

    async def reset_session(self, operator_id: str | None = None) -> bool:
        """Сбрасывает сессию пропущенных задач в Core API."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/triage/session/reset"
        params = {"operator_id": operator_id} if operator_id else {}
        async with session.post(url, params=params) as resp:
            return resp.status == 200

    async def submit_command(
        self,
        command_type: str,
        target: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        mode: str = "auto",
        priority: int = 5,
        idempotency_key: str | None = None,
        initiator: str | None = None,
        source: str = "cli",
        auto_close_ticket: bool = True,
    ) -> dict[str, Any]:
        """Отправляет команду в единую шину Command Bus."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/commands"
        payload = {
            "type": command_type,
            "target": target or {},
            "params": params or {},
            "mode": mode,
            "priority": priority,
            "idempotency_key": idempotency_key,
            "initiator": initiator,
            "source": source,
            "auto_close_ticket": auto_close_ticket,
        }
        async with session.post(url, json=payload) as resp:
            if resp.status in (200, 202):
                return await resp.json()
            err_text = await resp.text()
            logger.error("Ошибка submit_command (HTTP %d): %s", resp.status, err_text)
            return {"status": "error", "error": err_text}

    async def get_command_status(self, job_id: str) -> dict[str, Any] | None:
        """Получает статус выполнения команды по job_id."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/commands/{job_id}"
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            return None

    async def confirm_command(
        self,
        job_id: str,
        decision: str = "approve",
        reason: str | None = None,
        operator: str | None = None,
    ) -> dict[str, Any]:
        """Отправляет решение оператора (HITL) по ожидающей задаче."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/commands/{job_id}/confirm"
        payload = {"decision": decision, "reason": reason, "operator": operator}
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                return await resp.json()
            return {"status": "error", "http_status": resp.status}

    async def get_audit_log(
        self,
        status_filter: str | None = None,
        command_type: str | None = None,
        initiator: str | None = None,
        task_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Получает историю выполнения команд из audit log."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/commands"
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status_filter:
            params["status"] = status_filter
        if command_type:
            params["command_type"] = command_type
        if initiator:
            params["initiator"] = initiator
        if task_id:
            params["task_id"] = task_id
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            return {"total": 0, "items": []}

    async def ai_summarize(
        self,
        task_id: int,
        task_name: str,
        task_desc: str = "",
        comments: list[dict[str, Any]] | None = None,
        bypass_cache: bool = False,
    ) -> dict[str, Any] | None:
        """Запрашивает структурированную AI-выжимку переписки через Core API AI Hub."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/ai/summarize"
        payload = {
            "task_id": task_id,
            "task_name": task_name,
            "task_desc": task_desc,
            "comments": comments or [],
            "bypass_cache": bypass_cache,
        }
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.debug("Ошибка ai_summarize (HTTP %d)", resp.status)
                return None
        except Exception as e:
            logger.debug("Сбой запроса ai_summarize: %s", e)
            return None

    async def ai_analyze(
        self,
        task_id: int,
        task_name: str,
        task_desc: str = "",
    ) -> dict[str, Any] | None:
        """Запрашивает глубокий AI-анализ инцидента через Core API AI Hub."""
        session = await self._get_session()
        url = f"{self.base_url}/api/v1/ai/analyze"
        payload = {
            "task_id": task_id,
            "task_name": task_name,
            "task_desc": task_desc,
        }
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logger.debug("Сбой запроса ai_analyze: %s", e)
            return None


