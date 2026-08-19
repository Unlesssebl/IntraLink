import base64
import logging
import os
import re
from typing import Any
import aiohttp

logger = logging.getLogger("helpdesk_agent.api")


def parse_custom_fields(data_xml: str | None) -> dict[str, str]:
    """Парсит XML кастомных полей IntraService в плоский словарь {field_id: value}."""
    if not data_xml:
        return {}
    fields = {}
    matches = re.findall(r'<field id="(\d+)">([^<]*)</field>', data_xml)
    for fid, val in matches:
        if val.strip():
            fields[fid] = val.strip()
    return fields


class IntraServiceClient:
    def __init__(
        self,
        base_url: str | None = None,
        login: str | None = None,
        password: str | None = None,
        ssl_verify: bool = False,
    ):
        self.base_url = (
            base_url or os.getenv("INTRASERVICE_URL", "https://servicedesk.corporate.loc/api/")
        ).rstrip("/")
        self.login = login or os.getenv("INTRASERVICE_LOGIN", "IntraService_dev")
        self.password = password or os.getenv("INTRASERVICE_PASSWORD", "85_wW8EuOyYaw+xv6")
        self.ssl_verify = ssl_verify
        self._session: aiohttp.ClientSession | None = None

        auth_str = f"{self.login}:{self.password}"
        self.auth_header = "Basic " + base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self.ssl_verify)
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        endpoint: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any | None:
        session = await self._get_session()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.auth_header,
        }

        try:
            async with session.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status in (200, 201):
                    try:
                        return await response.json()
                    except Exception:
                        return {}
                elif response.status == 204:
                    return {}
                else:
                    text = await response.text()
                    logger.error(
                        "Ошибка IntraService API [%s] %s: %s", response.status, url, text[:200]
                    )
                    return None
        except Exception as e:
            logger.error("Сетевая ошибка при обращении к %s: %s", url, e)
            return None

    async def get_service_catalog(self) -> list[dict[str, Any]]:
        """Загружает официальное дерево каталога услуг."""
        res = await self._request(
            "service",
            params={"include": "parentid", "for": "createtask", "pagesize": "1000"},
        )
        if isinstance(res, dict):
            return res.get("Services", [])
        elif isinstance(res, list):
            return res
        return []

    async def get_tasks_by_filter(
        self, filter_id: int, page: int = 1, page_size: int = 25
    ) -> list[dict[str, Any]]:
        """Получает список открытых задач по ID фильтра (например, 984 для 1-й линии)."""
        res = await self._request(
            "task",
            params={
                "filterid": str(filter_id),
                "include": "status,customfields,service,comments",
                "pagesize": str(page_size),
                "page": str(page),
            },
        )
        if isinstance(res, dict):
            return res.get("Tasks", [])
        elif isinstance(res, list):
            return res
        return []

    async def get_task_details(self, task_id: int) -> dict[str, Any] | None:
        """Получает полную информацию по конкретной заявке с комментариями и кастомными полями."""
        res = await self._request(
            f"task/{task_id}",
            params={"include": "customfields,status,service,comments"},
        )
        if isinstance(res, dict):
            task = res.get("Task", res)
            if task:
                task["_parsed_fields"] = parse_custom_fields(task.get("Data"))
            return task
        return None

    async def get_task_history(self, task_id: int) -> list[dict[str, Any]]:
        """Получает историю изменений заявки."""
        res = await self._request(
            f"taskhistory",
            params={"taskid": str(task_id), "pagesize": "50"},
        )
        if isinstance(res, dict):
            return res.get("TaskHistory", [])
        elif isinstance(res, list):
            return res
        return []

    async def add_comment(self, task_id: int, comment: str, is_private: bool = False) -> bool:
        """Добавляет комментарий к заявке в IntraService."""
        res = await self._request(
            f"task/{task_id}",
            method="PUT",
            json_data={
                "Id": task_id,
                "Comment": comment,
                "IsPrivateComment": is_private,
            },
        )
        return res is not None

    async def update_status(self, task_id: int, status_id: int) -> bool:
        """Обновляет статус заявки (27=В работе, 35=Требует уточнения, 30=Отменена, 29=Выполнена и т.д.)."""
        res = await self._request(
            f"task/{task_id}",
            method="PUT",
            json_data={
                "Id": task_id,
                "StatusId": status_id,
            },
        )
        return res is not None

    async def assign_task(self, task_id: int, performer_id: int) -> bool:
        """Назначает исполнителя заявки."""
        res = await self._request(
            f"task/{task_id}",
            method="PUT",
            json_data={
                "Id": task_id,
                "PerformerId": performer_id,
            },
        )
        return res is not None

    async def add_expenses(self, task_id: int, minutes: int = 15) -> bool:
        """Списывает трудозатраты (в минутах) на заявку."""
        res = await self._request(
            "taskexpenses",
            method="POST",
            json_data={"TaskId": task_id, "Minutes": minutes},
        )
        return res is not None
