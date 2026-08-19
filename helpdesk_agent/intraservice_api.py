import base64
import logging
import os
import re
from typing import Any
import aiohttp
from normalizer import normalize_pc_name, is_valid_pc_name

logger = logging.getLogger("helpdesk_agent.api")

# Полная таблица маппинга кастомных полей IntraService для всех типов форм
FIELD_NAME_MAP = {
    # Форма А (Рабочие станции и оргтехника)
    "1087": "Кабинет / Локация",
    "1088": "Телефон",
    "1089": "Имя ПК",
    "1091": "Подразделение",
    "1092": "ФИО пользователя",
    "1198": "Должность",
    
    # Форма B (Принтеры и МФУ)
    "1103": "Модель принтера",
    "1104": "IP принтера",
    "1112": "Имя ПК",
    
    # Форма C (Программное обеспечение, 1С, доступы)
    "1202": "Телефон",
    "1203": "Имя ПК",
    "1206": "Должность / Подразделение",
    "1494": "Email",
    
    # Прочие формы
    "1509": "Доп. информация",
}

PHONE_REGEX = re.compile(r"^\+?[0-9\s\-_]{2,15}$")


def parse_custom_fields(data_xml: str | None) -> dict[str, Any]:
    """
    Парсит XML кастомных полей IntraService и извлекает стандартизированные
    сущности (Имя ПК, Телефон, Кабинет, Email, Подразделение) с нормализацией.
    """
    if not data_xml:
        return {
            "raw": {},
            "friendly": {},
            "room": "",
            "phone": "",
            "pc_name": "",
            "department": "",
            "user_name": "",
            "email": "",
        }

    raw_fields = {}
    friendly_fields = {}
    matches = re.findall(r'<field id="(\d+)">([^<]*)</field>', data_xml)

    pc_name = ""
    phone = ""
    room = ""
    department = ""
    user_name = ""
    email = ""

    for fid, val in matches:
        v = val.strip()
        if not v:
            continue
        raw_fields[fid] = v
        f_name = FIELD_NAME_MAP.get(fid, f"Поле_{fid}")
        friendly_fields[f_name] = v

        # 1. Точный маппинг по ID
        if fid in ("1089", "1112", "1203"):
            norm_pc = normalize_pc_name(v)
            if norm_pc:
                pc_name = norm_pc
        elif fid in ("1088", "1202"):
            phone = v
        elif fid in ("1087",):
            room = v
        elif fid in ("1091", "1206"):
            department = v
        elif fid in ("1092",):
            user_name = v
        elif fid in ("1494",):
            email = v

    # 2. Интеллектуальный эвристический fallback (если ID не совпал)
    for fid, v in raw_fields.items():
        if not pc_name and is_valid_pc_name(v):
            pc_name = normalize_pc_name(v) or ""
        if not phone and PHONE_REGEX.match(v) and not is_valid_pc_name(v):
            phone = v
        if not email and "@" in v:
            email = v
        if not room and any(w in v.lower() for w in ["каб", "комн", "цмк", "абк", "склад", "цех", "аквариум"]):
            room = v

    return {
        "raw": raw_fields,
        "friendly": friendly_fields,
        "room": room,
        "phone": phone,
        "pc_name": pc_name,
        "department": department,
        "user_name": user_name,
        "email": email,
    }


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
                    text_resp = await response.text()
                    logger.warning(
                        "Ошибка IntraService API [%s] %s: %s",
                        response.status,
                        url,
                        text_resp[:200],
                    )
                    return None
        except Exception as e:
            logger.error("Сетевой сбой при обращении к IntraService (%s): %s", endpoint, e)
            return None

    async def get_service_catalog(self) -> list[dict[str, Any]]:
        """Получает дерево каталога услуг."""
        res = await self._request("service", params={"for": "createtask", "pagesize": "1000"})
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

    async def get_tasks_by_status(
        self, status_ids: list[int], page: int = 1, page_size: int = 50
    ) -> list[dict[str, Any]]:
        """Получает список закрытых/отмененных задач по ID статусов (29, 30)."""
        status_str = ",".join(str(s) for s in status_ids)
        res = await self._request(
            "task",
            params={
                "statusids": status_str,
                "statusid": status_str,
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
            params={"include": "status,customfields,service,comments,attachments"},
        )
        if not res:
            return None

        # Разворачиваем объект Task, если API вернул обертку
        if "Task" in res and isinstance(res["Task"], dict):
            raw_wrapper = res
            res = raw_wrapper["Task"]
            for k in ["Priorities", "Services", "Statuses", "Users"]:
                if k in raw_wrapper:
                    res[f"_{k}"] = raw_wrapper[k]

        # Обогащаем распарсенными кастомными полями
        custom_xml = res.get("Data")
        parsed_fields = parse_custom_fields(custom_xml)
        res["_parsed_fields"] = parsed_fields.get("friendly", {})
        res["_field_meta"] = parsed_fields

        # Проверяем вложения
        raw_att = res.get("Attachments") or res.get("Files") or []
        if isinstance(raw_att, str):
            attachments = [{"FileName": x.strip()} for x in raw_att.split(",") if x.strip()]
        elif isinstance(raw_att, list):
            attachments = [
                x if isinstance(x, dict) else {"FileName": str(x)}
                for x in raw_att
            ]
        else:
            attachments = []

        res["_attachments_list"] = attachments
        res["_has_attachments"] = len(attachments) > 0
        return res

    async def get_task_history(self, task_id: int) -> list[dict[str, Any]]:
        """Получает историю изменений и комментарии задачи (TaskLifetimes)."""
        res = await self._request(
            "tasklifetime",
            params={"taskid": str(task_id)},
        )
        if isinstance(res, dict):
            return res.get("TaskLifetimes", [])
        elif isinstance(res, list):
            return res
        return []

    async def add_comment(self, task_id: int, comment: str, is_private: bool = False) -> bool:
        """Добавляет комментарий к заявке."""
        payload = {
            "Id": task_id,
            "Comments": comment,
            "IsPublic": not is_private,
        }
        res = await self._request(f"task/{task_id}", method="PUT", json_data=payload)
        return res is not None

    async def update_status(self, task_id: int, status_id: int) -> bool:
        """Обновляет статус заявки."""
        payload = {
            "Id": task_id,
            "StatusId": status_id,
        }
        res = await self._request(f"task/{task_id}", method="PUT", json_data=payload)
        return res is not None

    async def add_expenses(self, task_id: int, minutes: int) -> bool:
        """Списывает трудозатраты по заявке в минутах."""
        hours = round(minutes / 60.0, 2)
        payload = {
            "TaskId": task_id,
            "Hours": hours,
            "Description": "Автоматическое выполнение в Helpdesk Agent (AGY)",
        }
        res = await self._request("taskexpenses", method="POST", json_data=payload)
        return res is not None

    async def download_attachment_file(self, task_id: int, file_id: int, save_path: str) -> bool:
        """Скачивает файл вложения из IntraService."""
        session = await self._get_session()
        url = f"{self.base_url}/task/{task_id}/attachment/{file_id}"
        headers = {"Authorization": self.auth_header}

        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
                    with open(save_path, "wb") as f:
                        f.write(await resp.read())
                    return True
                logger.warning("Не удалось скачать вложение %s для задачи #%s (код %s)", file_id, task_id, resp.status)
                return False
        except Exception as e:
            logger.error("Ошибка скачивания вложения #%s: %s", file_id, e)
            return False
