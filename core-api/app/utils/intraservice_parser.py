"""
Модуль парсинга и нормализации кастомных полей и вложений IntraService API.
"""

import logging
import re
from typing import Any

from app.utils.normalizer import (
    is_valid_pc_name,
    normalize_pc_name,
    extract_pc_names_from_text,
)

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
    # Создание учетных записей (Форма 1 - сервис 53)
    "1057": "Фамилия",
    "1058": "Имя",
    "1059": "Отчество",
    "1064": "Подразделение",
    "1065": "Должность",
    "1066": "Телефон",
    "1068": "Имя ПК",
    "1523": "Email / Руководитель",
    # Создание учетных записей (Форма 2)
    "1069": "Фамилия",
    "1070": "Имя",
    "1071": "Отчество",
    "1073": "Должность",
    "1075": "Телефон",
    "1078": "Подразделение",
    "1079": "Кабинет",
    "1120": "Имя ПК",
    # Форма D (Периферия, оргтехника и акты списания)
    "1111": "Оборудование / Инвентарный номер",
    "1176": "Имя ПК",
    # Прочие формы
    "1509": "Доп. информация",
}

PHONE_REGEX = re.compile(r"^\+?[0-9\s\-_]{2,15}$")


def parse_custom_fields(data_xml: str | None) -> dict[str, Any]:
    """
    Парсит XML кастомных полей IntraService и извлекает стандартизированные
    сущности (Имя ПК, Инвентарный номер, Телефон, Кабинет, Email, Подразделение) с нормализацией.
    """
    if not data_xml:
        return {
            "raw": {},
            "friendly": {},
            "room": "",
            "phone": "",
            "pc_name": "",
            "inventory_number": "",
            "department": "",
            "user_name": "",
            "email": "",
        }

    raw_fields = {}
    friendly_fields = {}
    matches = re.findall(r'<field id="(\d+)">([^<]*)</field>', data_xml)

    pc_name = ""
    inventory_number = ""
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
        if fid in ("1089", "1112", "1203", "1120", "1176", "1068"):
            pcs = extract_pc_names_from_text(v)
            if pcs:
                pc_name = ", ".join(pcs)
            else:
                norm_pc = normalize_pc_name(v)
                if norm_pc:
                    pc_name = norm_pc
        elif fid in ("1111",):
            inventory_number = v
        elif fid in ("1088", "1202", "1075", "1066"):
            phone = v
        elif fid in ("1087", "1079"):
            room = v
        elif fid in ("1091", "1206", "1078", "1064"):
            department = v
        elif fid in ("1092",):
            user_name = v
        elif fid in ("1494", "1523"):
            email = v

    if not user_name:
        s = raw_fields.get("1057") or raw_fields.get("1069") or ""
        n = raw_fields.get("1058") or raw_fields.get("1070") or ""
        p = raw_fields.get("1059") or raw_fields.get("1071") or ""
        if s and n:
            user_name = f"{s} {n}" + (f" {p}" if p else "")

    # 2. Интеллектуальный эвристический fallback (если ID не совпал)
    for fid, v in raw_fields.items():
        if not pc_name:
            pcs = extract_pc_names_from_text(v)
            if pcs:
                pc_name = ", ".join(pcs)
            elif is_valid_pc_name(v):
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
        "inventory_number": inventory_number,
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

    # Дополнительный интеллектуальный поиск хоста в теме и описании задачи
    if not parsed_fields.get("pc_name"):
        text_hosts = extract_pc_names_from_text(
            f"{res.get('Name', '')} {res.get('Description', '')}"
        )
        if text_hosts:
            parsed_fields["pc_name"] = text_hosts[0]
            if "friendly" in parsed_fields and isinstance(
                parsed_fields["friendly"], dict
            ):
                parsed_fields["friendly"]["Имя ПК"] = text_hosts[0]

    res["_parsed_fields"] = parsed_fields.get("friendly", {})
    res["_field_meta"] = parsed_fields

    # Проверяем вложения
    raw_att = res.get("Attachments") or res.get("Files") or []
    attachments = []
    if isinstance(raw_att, str):
        for item in raw_att.split(","):
            item = item.strip()
            if not item:
                continue
            if "|" in item:
                fid_part, name_part = item.split("|", 1)
                fid = int(fid_part.strip()) if fid_part.strip().isdigit() else None
                fname = name_part.strip()
            else:
                fid = None
                fname = item
            attachments.append({
                "Id": fid,
                "id": fid,
                "FileName": fname,
                "name": fname,
            })
    elif isinstance(raw_att, list):
        for x in raw_att:
            if isinstance(x, dict):
                fid = x.get("Id") or x.get("id") or x.get("FileId")
                fname = x.get("FileName") or x.get("name") or str(x)
                if isinstance(fname, str) and "|" in fname and not fid:
                    fid_part, name_part = fname.split("|", 1)
                    if fid_part.strip().isdigit():
                        fid = int(fid_part.strip())
                        fname = name_part.strip()
                attachments.append({
                    "Id": fid,
                    "id": fid,
                    "FileName": fname,
                    "name": fname,
                    "size": x.get("Size") or x.get("size"),
                })
            else:
                s = str(x).strip()
                if "|" in s:
                    fid_part, name_part = s.split("|", 1)
                    fid = int(fid_part.strip()) if fid_part.strip().isdigit() else None
                    fname = name_part.strip()
                else:
                    fid = None
                    fname = s
                attachments.append({
                    "Id": fid,
                    "id": fid,
                    "FileName": fname,
                    "name": fname,
                })

    res["_attachments_list"] = attachments
    res["_has_attachments"] = len(attachments) > 0
    return res
