import html
import re
from typing import Any, Dict, List, Optional, Tuple

from core.intraservice.dto import ExtractedEntitiesDTO

# Field ID to Human Readable Name Mapping (IntraService forms catalog)
FIELD_NAME_MAP: Dict[str, str] = {
    # Form A: Workstations & Peripherals
    "1087": "Кабинет / Локация",
    "1088": "Телефон",
    "1089": "Имя ПК",
    "1091": "Подразделение",
    "1092": "ФИО пользователя",
    "1198": "Должность",
    # Form B: Printers & MFUs
    "1103": "Модель принтера",
    "1104": "IP принтера",
    "1112": "Имя ПК",
    # Form C: Software, 1C, Access rights
    "1202": "Телефон",
    "1203": "Имя ПК",
    "1206": "Должность / Подразделение",
    "1494": "Email",
    # Account Provisioning (Form 1 - Service 53)
    "1057": "Фамилия",
    "1058": "Имя",
    "1059": "Отчество",
    "1064": "Подразделение",
    "1065": "Должность",
    "1066": "Телефон",
    "1068": "Имя ПК",
    "1523": "Email / Руководитель",
    # Account Provisioning (Form 2)
    "1069": "Фамилия",
    "1070": "Имя",
    "1071": "Отчество",
    "1073": "Должность",
    "1075": "Телефон",
    "1078": "Подразделение",
    "1079": "Кабинет",
    "1120": "Имя ПК",
    # Form D: Hardware inventory
    "1111": "Оборудование / Инвентарный номер",
    "1176": "Имя ПК",
    # Directum (TaskType 4)
    "1015": "Телефон",
    "1016": "Имя ПК",
    "1017": "Организация",
    "1018": "Причина ошибки",
    "1019": "Виновник ошибки",
    "1020": "Этап ошибки",
    "1181": "ID заявки в Directum",
    "1519": "Email",
    # Directum User Account (TaskType 1018)
    "1121": "Фамилия",
    "1122": "Имя",
    "1123": "Отчество",
    "1128": "Подразделение",
    "1129": "Должность",
    "1130": "Телефон",
    "1132": "Имя ПК",
    "1133": "Табельный номер",
    "1134": "Сотрудник со схожими правами",
    "1135": "Выполняемые действия в Directum",
    "1180": "Установка Directum на ПК",
    "1488": "Логин (IT)",
    "1489": "Пароль (IT)",
    "1521": "Email",
    # Additional
    "1509": "Доп. информация",
}

PC_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{3,30}$")
# Matches multi-segment PC hostnames like PC-BUHG-05, WS-SALES-10, PC-12345, NTEMW1020
PC_EXTRACT_REGEX = re.compile(
    r"\b(?:[a-zA-Z]{2,10}(?:[-_][a-zA-Z0-9]{1,12})+|[a-zA-Z]{2,6}[\s\-_]?[0-9]{2,6})\b",
    re.IGNORECASE,
)


def normalize_pc_name(raw_pc: str) -> str:
    """Normalize workstation hostname to clean uppercase NetBIOS format."""
    if not raw_pc:
        return ""
    cleaned = raw_pc.strip().upper()
    cleaned = cleaned.split(".")[0]
    return cleaned


def extract_pc_names_from_text(text: str) -> List[str]:
    """Find potential PC hostnames embedded within text/descriptions."""
    if not text:
        return []
    matches = PC_EXTRACT_REGEX.findall(text)
    results: List[str] = []
    for m in matches:
        norm = normalize_pc_name(m)
        if norm and len(norm) >= 3 and norm not in results:
            results.append(norm)
    return results


def parse_custom_fields(data_xml: str | None) -> Tuple[ExtractedEntitiesDTO, Dict[str, str]]:
    """Parse custom field XML payload into typed entities and friendly key-value map."""
    if not data_xml:
        return ExtractedEntitiesDTO(), {}

    friendly_fields: Dict[str, str] = {}
    matches = re.findall(r'<field id="(\d+)">([^<]*)</field>', data_xml)

    pc_name = ""
    inventory_number = ""
    phone = ""
    room = ""
    department = ""
    user_name = ""
    email = ""

    raw_fields: Dict[str, str] = {}
    for fid, val in matches:
        v = val.strip()
        if not v:
            continue
        raw_fields[fid] = v
        f_name = FIELD_NAME_MAP.get(fid, f"Поле_{fid}")
        friendly_fields[f_name] = v

        if fid in ("1089", "1112", "1203", "1120", "1176", "1068", "1016", "1132"):
            norm = normalize_pc_name(v)
            if norm:
                pc_name = norm
            else:
                pcs = extract_pc_names_from_text(v)
                pc_name = ", ".join(pcs) if pcs else ""
        elif fid in ("1111",):
            inventory_number = v
        elif fid in ("1088", "1202", "1075", "1066", "1015", "1130"):
            phone = v
        elif fid in ("1087", "1079"):
            room = v
        elif fid in ("1091", "1206", "1078", "1064", "1128"):
            department = v
        elif fid in ("1092",):
            user_name = v
        elif fid in ("1494", "1523", "1519", "1521"):
            email = v

    if not user_name:
        parts = [
            raw_fields.get("1057") or raw_fields.get("1069") or raw_fields.get("1121") or "",
            raw_fields.get("1058") or raw_fields.get("1070") or raw_fields.get("1122") or "",
            raw_fields.get("1059") or raw_fields.get("1071") or raw_fields.get("1123") or "",
        ]
        constructed = " ".join(p.strip() for p in parts if p.strip())
        if constructed:
            user_name = constructed

    entities = ExtractedEntitiesDTO(
        pc_name=pc_name,
        phone=phone,
        room=room,
        department=department,
        user_name=user_name,
        email=email,
        inventory_number=inventory_number,
    )
    return entities, friendly_fields


def enrich_task_dict(task: Dict[str, Any]) -> Dict[str, Any]:
    """Attach parsed custom field entities to raw task dictionary."""
    if not isinstance(task, dict):
        return task

    xml_data = task.get("CustomFieldData")
    entities, friendly = parse_custom_fields(xml_data)

    task["entities"] = entities.model_dump()
    task["custom_fields"] = friendly

    # Fallback PC search in task name or description if not filled in form
    if not task["entities"]["pc_name"]:
        name_pcs = extract_pc_names_from_text(task.get("Name", ""))
        desc_pcs = extract_pc_names_from_text(task.get("Description", ""))
        candidates = list(dict.fromkeys(name_pcs + desc_pcs))
        if candidates:
            task["entities"]["pc_name"] = ", ".join(candidates)

    return task


def sanitize_ticket_description(text: Optional[str], max_chars: Optional[int] = None) -> str:
    """Clean HTML tags, convert breaks to newlines, strip heavy Base64 data-URIs, and truncate if requested."""
    if not text:
        return ""

    # 1. Replace inline Base64 data-URIs (embedded images that cause megabyte payloads)
    cleaned = re.sub(
        r"data:image\/[a-zA-Z0-9\+\-\.]+;base64,[a-zA-Z0-9+/=]+",
        "[встроенное изображение]",
        text,
        flags=re.IGNORECASE,
    )

    # 2. Convert standard block & line-break tags to linebreaks
    cleaned = re.sub(r"<\s*br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<\s*/p\s*>", "\n\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<\s*/div\s*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<\s*li\s*>", "• ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<\s*/li\s*>", "\n", cleaned, flags=re.IGNORECASE)

    # 3. Strip all remaining HTML tags
    cleaned = re.sub(r"<[^>]+>", "", cleaned)

    # 4. Decode HTML entities (&quot;, &amp;, &lt;, &gt;, &#39;, &nbsp;, etc.)
    cleaned = html.unescape(cleaned)

    # 5. Normalize whitespace (replace non-breaking space, collapse 3+ newlines to 2)
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = re.sub(r"\r\n|\r", "\n", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()

    # 6. Apply character limit if requested (e.g. for queue preview)
    if max_chars and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "\n\n[...описание сокращено для превью]"

    return cleaned

