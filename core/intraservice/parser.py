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

# ==============================================================================
# Enterprise Network Hardware Classifier & Normalizer (Domain SSOT)
# ==============================================================================

# 1. Homoglyphs, keyboard layout translation and transliteration tables (from v1 SSOT)
_HOMOGLYPHS_CYR = "ОСАЕРХМТКВУ"
_HOMOGLYPHS_LAT = "OCAEPXMTKWU"
_HOMOGLYPH_MAP = str.maketrans(_HOMOGLYPHS_CYR + _HOMOGLYPHS_CYR.lower(), _HOMOGLYPHS_LAT + _HOMOGLYPHS_LAT.lower())

_KEYBOARD_RU = "йцукенгшщзхъфывапролджэячсмитьбю.ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,"
_KEYBOARD_EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,./QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?"
_KEYBOARD_RU_TO_EN = str.maketrans(_KEYBOARD_RU, _KEYBOARD_EN)

_TRANSLIT_MAP = {
    "Н": "N", "Т": "T", "Е": "E", "М": "M", "В": "W", "W": "W",
    "К": "K", "З": "Z", "П": "P", "Л": "L", "Д": "D", "Р": "R",
    "С": "S", "О": "O", "А": "A", "Х": "H", "У": "U", "И": "I",
    "Б": "B", "Г": "G", "Ц": "C", "Ч": "CH", "Ш": "SH", "Щ": "SCH",
    "Ф": "F", "Э": "E", "Ю": "YU", "Я": "YA", "Ж": "ZH", "Й": "Y",
}

# Direct mapping of Cyrillic enterprise prefixes to canonical Latin codes
CYRILLIC_PREFIX_ALIASES: Dict[str, str] = {
    # Main corporate entities
    "ЗТЕ": "ZTE", "ЗТЭ": "ZTE", "ЗТЕО": "ZTEO", "ЗТЭО": "ZTEO",
    "КЗМ": "KZM", "КЗМК": "KZMK", "КМК": "KMK", "ТЛК": "TLK",
    "ТКТ": "TKT", "ТНТ": "TNT", "ИТТ": "ITT", "ТНМ": "TNM",
    "ГКТ": "GKT", "СКС": "SCS", "СЦС": "SCS",
    # Branches
    "ЗТ": "ZT", "КЗ": "KZ", "КМ": "KM", "ТЛ": "TL",
    "НТ": "NT", "ТТ": "TT", "ТМ": "TM", "ГК": "GK",
    # Printers / MFUs (Suffix 'P')
    "ЗТЕП": "ZTEP", "ЗТЭП": "ZTEP", "КЗМП": "KZMP", "КМКП": "KMKP",
    "ТЛКП": "TLKP", "ТКТП": "TKTP", "ТНТП": "TNTP", "ИТТП": "ITTP",
    "ИТП": "ITTP", "ТНМП": "TNMP", "ГКТП": "GKTP", "СКСП": "SCSP", "СЦСП": "SCSP",
    "ЗТП": "ZTP", "КЗП": "KZP", "КМП": "KMP", "ТЛП": "TLP",
    "НТП": "NTP", "ТТП": "TTP", "ТМП": "TMP", "ГКП": "GKP",
    # Domain aliases
    "НТЕМВ": "NTEMW", "НТЕМW": "NTEMW", "НТЕМ": "NTEMW", "НТЕНВ": "NTEMW",
    "КПК": "KPK", "НТЗ": "TKT",
}

# Official Workstation prefixes
MAIN_PC_PREFIXES = ["ZTE", "KZM", "KMK", "TLK", "TKT", "TNT", "ITT", "TNM", "GKT", "SCS"]
BRANCH_PC_PREFIXES = ["ZT", "KZ", "KM", "TL", "NT", "TT", "TM", "GK"]
DOMAIN_PC_PREFIXES = [
    "NTEMW", "KZMK", "ZTEO", "KPK", "NTZ", "TEMPO", "WKS", "PC", "WS",
    "ARM", "NB", "SRV", "RDS", "LAPTOP", "DESKTOP"
]
KNOWN_PC_PREFIXES = MAIN_PC_PREFIXES + BRANCH_PC_PREFIXES + DOMAIN_PC_PREFIXES

# Official Network Printer / MFU prefixes (Suffix 'P')
MAIN_PRINTER_PREFIXES = ["ZTEP", "KZMP", "KMKP", "TLKP", "TKTP", "TNTP", "ITTP", "TNMP", "GKTP", "SCSP"]
BRANCH_PRINTER_PREFIXES = ["ZTP", "KZP", "KMP", "TLP", "NTP", "TTP", "TMP", "GKP"]
KNOWN_PRINTER_PREFIXES = MAIN_PRINTER_PREFIXES + BRANCH_PRINTER_PREFIXES

# Whitelist of corporate workstation prefixes for sorting priority
CORP_HOST_PREFIXES = (
    "WKS-", "WKS", "NTEMW", "ARM-", "ARM", "АРМ-", "АРМ",
    "PC-", "PC", "WS-", "WS", "NB-", "NB",
    "LAPTOP-", "DESKTOP-", "SRV-", "RDS-",
) + tuple(MAIN_PC_PREFIXES) + tuple(BRANCH_PC_PREFIXES)

# Regex capturing PC tokens across Latin and Cyrillic with optional hyphens or spaces
CORP_PC_WHITELIST_REGEX = re.compile(
    r"\b(?:"
    r"(?:" + "|".join(KNOWN_PC_PREFIXES) + r")[\-_]?[0-9A-Za-z]+(?:[\-_][0-9A-Za-z]+)*|"
    r"[0-9A-Za-z]{2,12}[\-_](?:WKS|PC|WS|NB|ARM)"
    r")\b",
    re.IGNORECASE,
)
PC_EXTRACT_REGEX = CORP_PC_WHITELIST_REGEX

# Explicit context marker where preceding text indicates a workstation/computer
# Note: Supports Cyrillic and spaces between letters and digits (e.g. "ПК кзм0010", "на компе кзм 0010")
PC_CONTEXT_REGEX = re.compile(
    r"(?i)\b(?:пк|компьютер(?:а|у|ом)?|ноутбук(?:а|у|ом)?|хост(?:а|у|ом)?|комп(?:а|у|ом)?|"
    r"рабоч(?:ая|ей|ую)\s+станци(?:я|и|ю|ей)|workstation|host|laptop|desktop|арм)\s*[:#№\-–—]?\s*"
    r"([a-zA-Zа-яА-ЯёЁ0-9\-_]{2,25}(?:[\s\-_]+[0-9]{1,6})?)\b"
)

# Blacklist of hardware models, OS names, printer models and protocols that must NEVER be treated as PC hostnames
NON_PC_PATTERNS = re.compile(
    r"(?i)^(?:mf\d+|lbp\d+|fs[\-_]?\d+|dcp[\-_]?\w*|hl[\-_]?\w*|mfc[\-_]?\w*|sp[\-_]?\w*|"
    r"p[\-_]?\d{3,4}|m\d{3,4}|b\d{3}|c\d{3}|"
    r"win\d+|windows\d*|crypto\w*|usb\d*|tcp\d*|udp\d*|vlan\d*|port\d*|"
    r"spooler\w*|directum\w*|1c\w*|office\d*|excel\d*|word\d*|ip\d*|"
    r"taskalfa\w*|ecosys\w*|laserjet\w*|deskjet\w*|phaser\w*|workcentre\w*|"
    r"kyocera\w*|canon\w*|xerox\w*|brother\w*|pantum\w*|lexmark\w*|epson\w*)$"
)


def _split_prefix_and_number(name: str) -> tuple[str, str]:
    """Split alphanumeric device token into prefix and numeric part."""
    cleaned = name.strip()
    m = re.fullmatch(r"([A-Za-zА-Яа-яёЁ\-_]+)[\s\-_]*(\d+)", cleaned)
    if m:
        prefix = re.sub(r"[\s\-_]+", "", m.group(1))
        return prefix, m.group(2)
    m_num = re.fullmatch(r"\d+", cleaned)
    if m_num:
        return "", m_num.group(0)
    return cleaned, ""


def _transliterate_prefix(prefix: str) -> str:
    """Normalize and transliterate prefix via homoglyphs, keyboard layout and enterprise dictionary."""
    p_upper = prefix.upper().strip()
    if p_upper in CYRILLIC_PREFIX_ALIASES:
        return CYRILLIC_PREFIX_ALIASES[p_upper]

    switched = p_upper.translate(_KEYBOARD_RU_TO_EN).upper()
    if switched in KNOWN_PC_PREFIXES or switched in KNOWN_PRINTER_PREFIXES:
        return switched

    res = []
    for ch in p_upper:
        if ch in _TRANSLIT_MAP:
            res.append(_TRANSLIT_MAP[ch])
        else:
            res.append(ch.translate(_HOMOGLYPH_MAP))
    translit_str = "".join(res)

    if translit_str in CYRILLIC_PREFIX_ALIASES:
        return CYRILLIC_PREFIX_ALIASES[translit_str]
    return translit_str


def is_valid_pc_name(name: str | None) -> bool:
    """Validate whether token is a corporate workstation hostname."""
    if not name:
        return False
    norm = normalize_pc_name(name)
    if not norm or len(norm) < 4:
        return False
    if norm.isdigit() or not any(ch.isdigit() for ch in norm):
        return False
    if norm in ("NTZ-TEMPO", "TEMPO", "KZMK-TEMPO", "ZTE-TEMPO", "ZTEO-TEMPO"):
        return False
    # Reject printer prefixes (suffix 'P')
    if any(norm.upper().startswith(p) for p in KNOWN_PRINTER_PREFIXES):
        return False
    # Check known PC prefixes
    return any(norm.upper().startswith(p) for p in KNOWN_PC_PREFIXES)


def is_valid_printer_name(name: str | None) -> bool:
    """Validate whether token is a corporate network printer/MFU hostname (with suffix 'P')."""
    if not name:
        return False
    cleaned = name.strip().strip(" #№.,;:()")
    prefix, number = _split_prefix_and_number(cleaned)
    if not prefix or not number:
        return False
    translit_prefix = _transliterate_prefix(prefix).upper()
    if translit_prefix in KNOWN_PC_PREFIXES and not translit_prefix.endswith("P"):
        return False
    return translit_prefix in KNOWN_PRINTER_PREFIXES


def normalize_pc_name(raw_pc: str) -> str:
    """Normalize workstation hostname to clean uppercase format (e.g. KZM0010, WKS-XXXX or NTEMW1020).

    Strips FQDN domain suffixes, spaces, special symbols, and converts pure digits or
    wks variations into canonical WKS-XXXX notation. Handles Cyrillic prefixes (КЗМ -> KZM).
    Rejects printer models (e.g. MF3010, FS4200, DCP-L2500) and OS tokens.
    """
    if not raw_pc:
        return ""
    cleaned = raw_pc.strip()
    # Strip FQDN domain suffix
    cleaned = cleaned.split(".")[0].strip()
    # Remove leading descriptive labels like "ПК: 1234", "хост #1234", "компьютер: ", "ПК kzm0010"
    cleaned = re.sub(
        r"^(?:пк|хост|ноут|компьютер|комп|arm|арм|host|рабочая\s+станция|workstation)\s*[:#№\s\-–—]*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip(" #№.,;:()")
    if not cleaned:
        return ""

    # Rejection of hardware models and software tokens
    if NON_PC_PATTERNS.match(cleaned):
        return ""

    # Check if purely digits (e.g. "1020" or "0102") -> canonical WKS-XXXX
    if cleaned.isdigit():
        return f"WKS-{cleaned}"

    # Handle Russian homoglyph "АРМ" -> ARM
    if cleaned.upper().startswith("АРМ"):
        cleaned = "ARM" + cleaned[3:]

    # Handle WKS variants: wks_1020, wks-1020, wks1020, wks 1020 -> WKS-1020
    m_wks = re.fullmatch(r"(?i)wks[\s\-_]*(\d+)", cleaned)
    if m_wks:
        return f"WKS-{m_wks.group(1)}"

    # Handle PC variants: pc_1020, pc 1020 -> PC-1020
    m_pc = re.fullmatch(r"(?i)pc[\s\-_]+(\d+)", cleaned)
    if m_pc:
        return f"PC-{m_pc.group(1)}"

    # Handle ARM variants: arm_1020, arm 1020 -> ARM-1020
    m_arm = re.fullmatch(r"(?i)arm[\s\-_]+(\d+)", cleaned)
    if m_arm:
        return f"ARM-{m_arm.group(1)}"

    # Handle NB variants: nb_1020, nb 1020 -> NB-1020
    m_nb = re.fullmatch(r"(?i)nb[\s\-_]+(\d+)", cleaned)
    if m_nb:
        return f"NB-{m_nb.group(1)}"

    # Preserve compound hostnames with hyphens/underscores (e.g. PC-BUHG-05, WS_FIN_04, BUH-01, WS-ACC-01, PC-SALES-10)
    if re.fullmatch(r"(?i)[A-Za-z0-9]{2,10}[\-_][A-Za-z0-9\-_]+", cleaned):
        return cleaned.upper()

    # Split into prefix and number
    prefix, number = _split_prefix_and_number(cleaned)
    if prefix and number:
        norm_prefix = _transliterate_prefix(prefix)
        # Reject printer prefix
        if norm_prefix.upper() in KNOWN_PRINTER_PREFIXES:
            return ""
        if norm_prefix.upper() in ("WKS", "PC", "ARM", "NB", "WS"):
            return f"{norm_prefix.upper()}-{number}"
        if norm_prefix.upper() in KNOWN_PC_PREFIXES:
            return f"{norm_prefix.upper()}{number}"

    # Fallback to transliterated prefix or uppercase
    if prefix:
        norm_prefix = _transliterate_prefix(prefix)
        if norm_prefix.upper() in KNOWN_PRINTER_PREFIXES:
            return ""
        if number:
            return f"{norm_prefix.upper()}{number}"

    return cleaned.upper()


def extract_pc_names_from_text(text: str) -> List[str]:
    """Find potential PC hostnames embedded within text/descriptions.

    Uses full enterprise classifier patterns (KZM, TNT, ZTE, KMK, TLK, NTEMW, WKS-, PC-),
    explicit contextual cues ('ПК kzm0010', 'компьютер: user-pc'). Rejects all printer models
    (DCP, HL, MF, LBP, FS, Pantum, Xerox, etc.) and printer queue names (KZMP, SCSP).
    """
    if not text:
        return []
    results: List[str] = []

    # 1. First pass: explicit context markers ('на ПК 1020', 'компьютер: buh-01', 'ПК kzm0010', 'к пк кзм 0010')
    for m in PC_CONTEXT_REGEX.finditer(text):
        token = m.group(1).strip()
        if NON_PC_PATTERNS.match(token):
            continue
        norm = normalize_pc_name(token)
        if norm and len(norm) >= 3 and not is_valid_printer_name(norm) and norm not in results:
            results.append(norm)

    # 2. Second pass: search for tokens with alphanumeric prefix + digits (KZM0010, TNT0088, SCSP0001)
    token_pattern = re.compile(r"(?i)\b([a-zA-Zа-яА-ЯёЁ]{2,6})[\s\-_]?([0-9]{2,6})\b")
    for m in token_pattern.finditer(text):
        raw_token = f"{m.group(1)}{m.group(2)}"
        if NON_PC_PATTERNS.match(raw_token):
            continue
        norm = normalize_pc_name(raw_token)
        if norm and is_valid_pc_name(norm) and not is_valid_printer_name(norm) and norm not in results:
            results.append(norm)

    # 3. Third pass: corporate whitelist pattern matches
    for m in CORP_PC_WHITELIST_REGEX.finditer(text):
        token = m.group(0).strip()
        if NON_PC_PATTERNS.match(token):
            continue
        if not (any(ch.isdigit() for ch in token) or token.upper().startswith(("SRV-", "RDS-"))):
            continue
        norm = normalize_pc_name(token)
        if norm and len(norm) >= 3 and not is_valid_printer_name(norm) and norm not in results:
            results.append(norm)

    # Prioritize corporate standard hostnames over arbitrary tokens
    def _host_priority(host: str) -> int:
        h_upper = host.upper()
        for idx, pref in enumerate(CORP_HOST_PREFIXES):
            if h_upper.startswith(pref):
                return idx
        return 999

    results.sort(key=_host_priority)
    return results


async def filter_valid_pcs_async(
    pc_candidates: List[str],
    redis_client: Optional[Any] = None,
    ad_pool: Optional[Any] = None,
) -> List[str]:
    """Filter extracted PC candidate names using fast AD/DNS ground-truth verification."""
    from core.ad.pool import is_valid_domain_computer

    valid: List[str] = []
    for cand in pc_candidates:
        if await is_valid_domain_computer(cand, redis_client=redis_client, ad_pool=ad_pool):
            valid.append(cand)
    return valid



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

PHONE_TEXT_REGEX = re.compile(
    r"(?i)(?:(?:тел(?:ефон)?|сот(?:овый)?|моб(?:ильный)?|внутр(?:енний)?|доб(?:авочный)?)[:\s.]*(\+?[0-9\s()_-]{3,25}\b))|"
    r"(\+?[78][\s\-(]?\d{3,4}[\s\-)]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}\b)"
)


def extract_phone_from_text(text: str) -> str:
    """Extract contact phone number avoiding collision with inventory/tab numbers."""
    if not text:
        return ""
    m = PHONE_TEXT_REGEX.search(text)
    if m:
        val = (m.group(1) or m.group(2) or "").strip()
        val = re.sub(r"[.,;]+$", "", val).strip()
        digits = re.sub(r"\D", "", val)
        if len(digits) >= 3 and not extract_pc_names_from_text(val):
            return val
    return ""


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
    matches = re.findall(r"""<field id=['"](\d+)['"]>([^<]*)</field>""", data_xml)

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

    # Extract onboarding and Directum custom fields
    last_name = raw_fields.get("1121") or raw_fields.get("1057") or raw_fields.get("1069") or ""
    first_name = raw_fields.get("1122") or raw_fields.get("1058") or raw_fields.get("1070") or ""
    middle_name = raw_fields.get("1123") or raw_fields.get("1059") or raw_fields.get("1071") or ""
    title = (
        raw_fields.get("1129")
        or raw_fields.get("1198")
        or raw_fields.get("1065")
        or raw_fields.get("1073")
        or ""
    )
    company = raw_fields.get("1017") or ""
    tab_number = raw_fields.get("1133") or ""
    similar_user = raw_fields.get("1134") or ""
    install_directum = raw_fields.get("1180") or ""
    directum_actions = raw_fields.get("1135") or ""
    it_login = raw_fields.get("1488") or ""
    it_password = raw_fields.get("1489") or ""
    directum_task_id = raw_fields.get("1181") or ""

    if not user_name:
        parts = [last_name, first_name, middle_name]
        constructed = " ".join(p.strip() for p in parts if p.strip())
        if constructed:
            user_name = constructed

    if user_name and not (last_name and first_name):
        fio_parts = user_name.split()
        if len(fio_parts) >= 2:
            last_name = last_name or fio_parts[0]
            first_name = first_name or fio_parts[1]
            if len(fio_parts) >= 3:
                middle_name = middle_name or " ".join(fio_parts[2:])

    if not target_user:
        target_user = it_login or user_name

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
        first_name=first_name,
        last_name=last_name,
        middle_name=middle_name,
        title=title,
        company=company,
        tab_number=tab_number,
        similar_user=similar_user,
        install_directum=install_directum,
        directum_actions=directum_actions,
        it_login=it_login,
        it_password=it_password,
        directum_task_id=directum_task_id,
    )
    return entities, friendly_fields


def enrich_task_dict(task: Dict[str, Any]) -> Dict[str, Any]:
    """Attach parsed custom field entities to raw task dictionary with deterministic hardware fallbacks.

    Tier 1: Structured XML CustomFieldData (0 ms).
    Tier 3: Strict deterministic host, printer queue/IP and phone normalizers (0 ms).
    Safe for bulk operations (poller / 200 items batch queries).
    """
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
            # Single best corporate candidate (never comma-join multiple items)
            task["entities"]["pc_name"] = candidates[0]

    # 2. Contextual Printer Address vs PC IP search
    addrs = extract_printer_addresses_from_text(full_text)
    # Filter out home/loopback subnets (192.168.x.x, 127.0.0.1, 169.254.x.x)
    corp_addrs = [
        a for a in addrs
        if not (a.startswith("192.168.") or a.startswith("127.") or a.startswith("169.254."))
    ]

    has_print_context = any(
        kw in full_text.lower()
        for kw in ("принтер", "мфу", "печать", "печата", "сканер", "scsp", "ittp", "kmkp", "kzmp", "kyocera", "canon", "hp ")
    )

    if corp_addrs:
        first_addr = corp_addrs[0]
        # Check if address is explicitly qualified as PC host IP (e.g. "на ПК 10.244.1.20", "компьютер 10.x")
        pc_ip_match = re.search(r"(?i)(?:пк|хост|компьютер|arm|арм|на)\s+(" + re.escape(first_addr) + r")", full_text)
        if pc_ip_match and not task["entities"]["pc_name"]:
            task["entities"]["pc_name"] = first_addr
        elif has_print_context and not task["entities"]["printer_address"]:
            task["entities"]["printer_address"] = first_addr

    # 3. Fallback Printer Model search in name/description
    if not task["entities"]["printer_model"]:
        model = extract_printer_model_from_text(full_text)
        if not model:
            # Catch standalone model tokens like MF3010, FS4200, P2500, M402
            m_token = re.search(r"(?i)\b(?:mf[\s\-_]?\d{3,4}|fs[\-_]?\d{3,4}|p[\-_]?\d{4}|m\d{3,4})\b", full_text)
            if m_token:
                model = m_token.group(0).upper().replace(" ", "")
        if model:
            task["entities"]["printer_model"] = model

    # 4. Fallback Target User / Login search in name/description
    if not task["entities"]["target_user"]:
        u = extract_target_user_from_text(full_text)
        if u:
            task["entities"]["target_user"] = u
        elif task.get("ApplicantName"):
            task["entities"]["target_user"] = str(task["ApplicantName"]).strip()
        elif task["entities"].get("user_name"):
            task["entities"]["target_user"] = task["entities"]["user_name"]

    # 5. Fallback Phone search (strictly formatted numbers with prefix or +7/8)
    if not task["entities"]["phone"]:
        p = extract_phone_from_text(full_text)
        if p:
            task["entities"]["phone"] = p

    return task


async def enrich_task_dict_async(
    task: Dict[str, Any],
    ai_client: Optional[Any] = None,
    timeout_sec: float = 2.5,
) -> Dict[str, Any]:
    """Asynchronously enrich task dictionary using the Three-Tier Hybrid Pipeline:

    - Tier 1: Exact XML CustomFieldData parsing (< 0.1 ms).
    - Tier 2: AI Fast NER (LiteLLM Gateway / Ollama) for unstructured natural text if facts missing.
    - Tier 3: Deterministic hardware host/printer normalizers.
    """
    if not isinstance(task, dict):
        return task

    # Run Tier 1 + Tier 3 fast pass
    task = enrich_task_dict(task)
    ent_dict = task.get("entities", {})

    name = str(task.get("Name") or "")
    desc = str(task.get("Description") or "")
    full_text = f"{name} {desc}".strip()

    # Check if critical entity fields are missing from XML and text is substantial
    facts_missing = (
        not ent_dict.get("last_name")
        or not ent_dict.get("first_name")
        or not ent_dict.get("title")
        or not ent_dict.get("department")
        or not ent_dict.get("similar_user")
        or not ent_dict.get("tab_number")
        or not ent_dict.get("pc_name")
        or not ent_dict.get("printer_address")
    )

    if facts_missing and len(full_text) >= 10:
        from core.intraservice.ai_extractor import get_ai_extractor

        try:
            extractor = get_ai_extractor(ai_client)
            ai_entities = await extractor.extract_entities(full_text, timeout_sec=timeout_sec)
            ai_dict = ai_entities.model_dump()

            for key, val in ai_dict.items():
                if val and not ent_dict.get(key):
                    ent_dict[key] = val

            # Keep target_user synchronized if newly identified
            if not ent_dict.get("target_user"):
                ent_dict["target_user"] = ent_dict.get("it_login") or ent_dict.get("user_name") or ""
        except Exception:
            pass

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

    @staticmethod
    async def extract_entities_async(
        description: str,
        custom_fields: Optional[Dict[str, str]] = None,
        title: str = "",
        ai_client: Optional[Any] = None,
        timeout_sec: float = 2.5,
    ) -> ExtractedEntitiesDTO:
        """Asynchronously extract entities using Three-Tier Hybrid Pipeline with AI fast pass."""
        raw_dict = {"Name": title, "Description": description, "CustomFieldData": None}
        enriched = await enrich_task_dict_async(raw_dict, ai_client=ai_client, timeout_sec=timeout_sec)
        return ExtractedEntitiesDTO.model_validate(enriched["entities"])

