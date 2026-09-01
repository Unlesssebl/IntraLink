"""
Модуль парсинга и нормализации кастомных полей и вложений IntraService API.
"""

import logging
import re
from typing import Any

from app.utils.normalizer import is_valid_pc_name, normalize_pc_name

logger = logging.getLogger("core_api.intraservice_parser")

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
    # Создание учетных записей
    "1069": "Фамилия",
    "1070": "Имя",
    "1071": "Отчество",
    "1073": "Должность",
    "1075": "Телефон",
    "1078": "Подразделение",
    "1079": "Кабинет",
    "1120": "Имя ПК",
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
        if fid in ("1089", "1112", "1203", "1120"):
            norm_pc = normalize_pc_name(v)
            if norm_pc:
                pc_name = norm_pc
        elif fid in ("1088", "1202", "1075"):
            phone = v
        elif fid in ("1087", "1079"):
            room = v
        elif fid in ("1091", "1206", "1078"):
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
        if not room and any(
            w in v.lower()
            for w in [
                "каб",
                "комн",
                "цмк",
                "абк",
                "склад",
                "цех",
                "аквариум",
            ]
        ):
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


def enrich_task_data(task: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Обогащает словарь задачи распарсенными кастомными полями, вложениями и мета-информацией.
    """
    if not task or not isinstance(task, dict):
        return task

    # Если задача пришла в обертке {"Task": {...}, "Users": ...}
    res = task
    if "Task" in res and isinstance(res["Task"], dict):
        raw_wrapper = res
        res = dict(raw_wrapper["Task"])
        for k in ["Priorities", "Services", "Statuses", "Users"]:
            if k in raw_wrapper:
                res[f"_{k}"] = raw_wrapper[k]

    # Парсим кастомные поля
    custom_xml = res.get("Data")
    parsed_fields = parse_custom_fields(custom_xml)
    res["_parsed_fields"] = parsed_fields.get("friendly", {})
    res["_field_meta"] = parsed_fields

    # Проверяем вложения
    raw_att = res.get("Attachments") or res.get("Files") or []
    if isinstance(raw_att, str):
        attachments = [
            {"FileName": x.strip()} for x in raw_att.split(",") if x.strip()
        ]
    elif isinstance(raw_att, list):
        attachments = [
            x if isinstance(x, dict) else {"FileName": str(x)} for x in raw_att
        ]
    else:
        attachments = []

    res["_attachments_list"] = attachments
    res["_has_attachments"] = len(attachments) > 0
    return res
