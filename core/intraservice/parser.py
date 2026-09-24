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

# Matches multi-segment PC hostnames like PC-BUHG-05, WS-SALES-10, PC-12345, NTEMW1020
PC_EXTRACT_REGEX = re.compile(
    r"\b(?:[a-zA-Z]{2,10}(?:[-_][a-zA-Z0-9]{1,12})+|[a-zA-Z]{2,6}[\s\-_]?[0-9]{2,6})\b",
    re.IGNORECASE,
)


def normalize_pc_name(raw_pc: str) -> str:
    """Normalize workstation hostname to clean uppercase format (e.g. WKS-XXXX or NTEMW1020).

    Strips FQDN domain suffixes, spaces, special symbols, and converts pure digits or
    wks variations into canonical WKS-XXXX notation. Preserves standard PC/WS/NTEMW prefixes.
    """
    if not raw_pc:
        return ""
    cleaned = raw_pc.strip()
    # Strip FQDN domain suffix
    cleaned = cleaned.split(".")[0].strip()
    # Remove leading descriptive labels like "ПК: 1234", "хост #1234", "компьютер: "
    cleaned = re.sub(
        r"^(?:пк|хост|ноут|компьютер|arm|арм|host)\s*[:#№]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Remove standalone labels before pure digits like "ПК 1020"
    cleaned = re.sub(
        r"^(?:пк|хост|ноут|компьютер|arm|арм)\s+(\d+)$",
        r"\1",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip(" #№.,;:()")
    if not cleaned:
        return ""

    # Check if purely digits (e.g. "1020" or "0102") -> canonical WKS-XXXX
    if cleaned.isdigit():
        return f"WKS-{cleaned}"

    # Handle WKS variants: wks_1020, wks-1020, wks1020, wks 1020 -> WKS-1020
    m_wks = re.fullmatch(r"(?i)wks[\s\-_]*(\d+)", cleaned)
    if m_wks:
        return f"WKS-{m_wks.group(1)}"

    return cleaned.upper()


def extract_pc_names_from_text(text: str) -> List[str]:
    """Find potential PC hostnames embedded within text/descriptions."""
    if not text:
        return []
    matches = PC_EXTRACT_REGEX.findall(text)
    results: List[str] = []
    for m in matches:
        # A valid computer hostname must contain at least one digit (avoids 'user_test', 'log_level')
        if not any(ch.isdigit() for ch in m):
            continue
        norm = normalize_pc_name(m)
        if norm and len(norm) >= 3 and norm not in results:
            results.append(norm)
    return results


def normalize_printer_address(raw_address: str) -> str:
    """Normalize IPv4 address or queue hostname for printers/MFUs.

    Fixes typos with commas and spaces in IP addresses and validates 4 octets.
    Converts queue names (e.g. SCSP 0001, ITTP 1000) to clean alphanumeric strings.
    """
    if not raw_address:
        return ""
    cleaned = raw_address.strip().strip(" #№.,;:()")
    if not cleaned:
        return ""

    # IPv4 detection and cleanup (e.g. 10,244 1.20 -> 10.244.1.20)
    ip_match = re.fullmatch(r"(\d{1,3})[.,\s]+(\d{1,3})[.,\s]+(\d{1,3})[.,\s]+(\d{1,3})", cleaned)
    if ip_match:
        octets = [int(p) for p in ip_match.groups()]
        if all(0 <= o <= 255 for o in octets):
            return ".".join(str(o) for o in octets)

    # Queue name / Hostname (e.g. "SCSP 0001" -> "scsp0001", "ittp-1000" -> "ittp1000")
    m_queue = re.fullmatch(r"([a-zA-Zа-яА-Я]+)[\s\-_]*(\d+)", cleaned)
    if m_queue:
        prefix = m_queue.group(1).lower()
        # Homoglyphs transliteration
        homoglyphs = str.maketrans("осваерхмтку", "ocwaerxmtku")
        prefix = prefix.translate(homoglyphs)
        return f"{prefix}{m_queue.group(2)}"

    return cleaned.split(".")[0].strip()


PRINTER_QUEUE_REGEX = re.compile(
    r"\b[a-zA-Zа-яА-Я]{2,6}p[\s\-_]?[0-9]{2,6}\b",
    re.IGNORECASE,
)
IPV4_REGEX = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b"
)


def extract_printer_addresses_from_text(text: str) -> List[str]:
    """Find potential IPv4 addresses and network queue names in text."""
    if not text:
        return []
    results: List[str] = []

    # 1. Search for IPv4 addresses
    for m in IPV4_REGEX.finditer(text):
        ip_candidate = m.group(0)
        # Avoid version numbers (e.g. 1С:Предприятие 8.3.27.2214)
        prefix = text[: m.start()].lower()
        if any(v in prefix[-20:] for v in ("верси", "ver", "build", "платформ", "1с", "1c")):
            continue
        octets = [int(p) for p in ip_candidate.split(".")]
        if all(0 <= o <= 255 for o in octets) and ip_candidate not in results:
            results.append(ip_candidate)

    # 2. Search for queue names (SCSP0001, ittp 1000)
    for m in PRINTER_QUEUE_REGEX.finditer(text):
        norm = normalize_printer_address(m.group(0))
        if norm and norm not in results:
            results.append(norm)

    return results


PRINTER_MODEL_PATTERNS = [
    # HP
    re.compile(
        r"\b(?:HP|Hewlett[- ]Packard)\s+(?:Color\s+)?(?:LaserJet|DeskJet|PageWide)?\s*(?:Pro|Enterprise)?\s*(?:MFP\s+)?[A-Z0-9_-]+\b",
        re.IGNORECASE,
    ),
    # Kyocera
    re.compile(r"\bKyocera\s+(?:Ecosys|TASKalfa|FS)?\s*[A-Z0-9_-]+\b", re.IGNORECASE),
    # Xerox
    re.compile(r"\bXerox\s+(?:Phaser|WorkCentre|VersaLink|AltaLink|B\d{3}|C\d{3}|[0-9]{4})\b", re.IGNORECASE),
    # Canon
    re.compile(r"\bCanon\s+(?:i-SENSYS|imageRUNNER|LBP|MF)?\s*[A-Z0-9_-]+\b", re.IGNORECASE),
    # Brother
    re.compile(r"\bBrother\s+(?:DCP|HL|MFC)?[- ][A-Z0-9_-]+\b", re.IGNORECASE),
    # Pantum
    re.compile(r"\bPantum\s+[A-Z0-9_-]+\b", re.IGNORECASE),
    # Label printers
    re.compile(r"\b(?:Zebra|Godex)\s+[A-Z0-9_-]+\b", re.IGNORECASE),
]


def extract_printer_model_from_text(text: str) -> str:
    """Find hardware vendor and printer model embedded in text."""
    if not text:
        return ""
    for pat in PRINTER_MODEL_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0).strip()
    return ""


TARGET_USER_REGEX = re.compile(
    r"(?i)(?:логин|учетная запись|учетка|пользователь|аккаунт|login|account|samaccountname)\s*[:=\-]\s*([a-zA-Z0-9_.\-]+)"
)


def extract_target_user_from_text(text: str) -> str:
    """Extract domain username / sAMAccountName from text."""
    if not text:
        return ""
    m = TARGET_USER_REGEX.search(text)
    if m:
        val = m.group(1).strip()
        if len(val) >= 3:
            return val
    return ""


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
    printer_address = ""
    printer_model = ""
    target_user = ""

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
        elif fid in ("1104",):
            norm_prn = normalize_printer_address(v)
            printer_address = norm_prn if norm_prn else v
        elif fid in ("1103",):
            printer_model = v
        elif fid in ("1488",):
            target_user = v
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

    if not target_user and user_name:
        target_user = user_name

    entities = ExtractedEntitiesDTO(
        pc_name=pc_name,
        phone=phone,
        room=room,
        department=department,
        user_name=user_name,
        email=email,
        inventory_number=inventory_number,
        printer_address=printer_address,
        printer_model=printer_model,
        target_user=target_user,
    )
    return entities, friendly_fields


def enrich_task_dict(task: Dict[str, Any]) -> Dict[str, Any]:
    """Attach parsed custom field entities to raw task dictionary with full fallbacks."""
    if not isinstance(task, dict):
        return task

    xml_data = task.get("CustomFieldData")
    entities, friendly = parse_custom_fields(xml_data)

    task_entities = entities.model_dump()
    task["entities"] = task_entities
    task["custom_fields"] = friendly

    name = str(task.get("Name") or "")
    desc = str(task.get("Description") or "")
    full_text = f"{name} {desc}".strip()

    # 1. Fallback PC search in task name or description
    if not task["entities"]["pc_name"]:
        candidates = extract_pc_names_from_text(full_text)
        if candidates:
            task["entities"]["pc_name"] = ", ".join(candidates)

    # 2. Fallback Printer Address search in name/description
    if not task["entities"]["printer_address"]:
        addrs = extract_printer_addresses_from_text(full_text)
        if addrs:
            task["entities"]["printer_address"] = addrs[0]

    # 3. Fallback Printer Model search in name/description
    if not task["entities"]["printer_model"]:
        model = extract_printer_model_from_text(full_text)
        if model:
            task["entities"]["printer_model"] = model

    # 4. Fallback Target User search in name/description
    if not task["entities"]["target_user"]:
        u = extract_target_user_from_text(full_text)
        if u:
            task["entities"]["target_user"] = u
        elif task.get("ApplicantName"):
            task["entities"]["target_user"] = str(task["ApplicantName"]).strip()
        elif task["entities"].get("user_name"):
            task["entities"]["target_user"] = task["entities"]["user_name"]

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


class IntraServiceParser:
    """Convenience facade for parsing IntraService workplace entities."""

    @staticmethod
    def extract_entities(
        description: str,
        custom_fields: Optional[Dict[str, str]] = None,
        title: str = "",
    ) -> ExtractedEntitiesDTO:
        raw_dict = {"Name": title, "Description": description, "CustomFieldData": None}
        enriched = enrich_task_dict(raw_dict)
        return ExtractedEntitiesDTO.model_validate(enriched["entities"])

