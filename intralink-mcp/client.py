"""
HTTP-клиент шлюза Core API Gateway для MCP сервера IntraLink.
"""

import os
import logging
from typing import Any
import httpx

logger = logging.getLogger("intralink_mcp.client")

CORE_API_URL = os.getenv("CORE_API_URL", "http://127.0.0.1:8000").rstrip("/")
BOT_API_KEY = os.getenv("BOT_API_KEY", "default-dev-bot-key")


class CoreApiClient:
    """Асинхронный клиент к эндпоинтам Core API Gateway."""

    def __init__(self, base_url: str = CORE_API_URL, api_key: str = BOT_API_KEY):
        self.base_url = base_url
        self.headers = {
            "X-Bot-Api-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_triage_batch(
        self,
        limit: int = 5,
        filter_id: int = 984,
        service_prefix: str | None = None,
        redirect_only: bool = False,
        include_skipped: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": limit,
            "filter_id": filter_id,
            "redirect_only": redirect_only,
            "include_skipped": include_skipped,
        }
        if service_prefix:
            params["service_prefix"] = service_prefix

        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            resp = await client.get("/api/v1/triage/batch", headers=self.headers, params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_ticket_details(self, task_id: int) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            resp = await client.get(f"/api/v1/triage/tasks/{task_id}", headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def apply_triage_decision(
        self,
        task_ids: list[int],
        status_id: int,
        comment: str = "",
        expenses: int = 0,
        dry_run: bool = False,
        confirmed_by_human: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "task_ids": task_ids,
            "status_id": status_id,
            "comment": comment,
            "expenses": expenses,
            "dry_run": dry_run,
            "confirmed_by_human": confirmed_by_human,
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            resp = await client.post("/api/v1/triage/apply", headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def diagnose_host(self, host: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20.0) as client:
            resp = await client.get(
                "/api/v1/admin/diagnose-host", headers=self.headers, params={"host": host}
            )
            resp.raise_for_status()
            return resp.json()

    async def search_kb(
        self, query: str, limit: int = 3, threshold: float = 0.70
    ) -> dict[str, Any]:
        payload = {"query": query, "limit": limit, "threshold": threshold}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20.0) as client:
            resp = await client.post("/api/v1/triage/rag/search", headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def submit_action_command(
        self,
        action_type: str,
        target: dict[str, Any],
        params: dict[str, Any] | None = None,
        mode: str = "auto",
    ) -> dict[str, Any]:
        payload = {
            "type": action_type,
            "target": target,
            "params": params or {},
            "mode": mode,
            "source": "mcp",
            "initiator": "antigravity_agent",
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            resp = await client.post("/api/v1/commands/submit", headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def list_skills(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=15.0) as client:
            resp = await client.get("/api/v1/skills", headers=self.headers)
            resp.raise_for_status()
            return resp.json()
