"""
Интеллектуальная нормализация и извлечение имён сетевых устройств (ПК и принтеров/МФУ)
для холдинга «ТЭМ-ПО» на основе официального регламента наименования оборудования (Таблица 1 и Таблица 2).

Принцип именования МФУ/принтеров:
К коду предприятия добавляется буква "P" (например, ZTEP, KZMP, KMKP, TLKP, TKTP, ITTP, TNMP, GKTP),
а для филиалов: ZTP, KZP, KMP, TLP, NTP, TTP, TMP, GKP.
"""

import logging
import re

logger = logging.getLogger("core_api.normalizer")

# 1. Таблица омоглифов
_HOMOGLYPHS_CYR = "ОСАЕРХМТКВУ"
_HOMOGLYPHS_LAT = "OCAEPXMTKWU"
_HOMOGLYPH_MAP = str.maketrans(
    _HOMOGLYPHS_CYR + _HOMOGLYPHS_CYR.lower(),
    _HOMOGLYPHS_LAT + _HOMOGLYPHS_LAT.lower(),
)

# 2. Таблица переключения раскладки клавиатуры (RU -> EN)
_KEYBOARD_RU = (
    "йцукенгшщзхъфывапролджэячсмитьбю.ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,"
)
_KEYBOARD_EN = (
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./QWERTYUIOP{}ASDFGHJKL:"
    + chr(34)
    + "ZXCVBNM<>?"
)
_KEYBOARD_RU_TO_EN = str.maketrans(_KEYBOARD_RU, _KEYBOARD_EN)

# 3. Полная транслитерация кириллических букв в латинские
_TRANSLIT_MAP = {
    "Н": "N",
    "Т": "T",
    "Е": "E",
    "М": "M",
    "В": "W",
    "W": "W",
    "К": "K",
    "З": "Z",
    "П": "P",
    "Л": "L",
    "Д": "D",
    "Р": "R",
    "С": "S",
    "О": "O",
    "А": "A",
    "Х": "H",
    "У": "U",
    "И": "I",
    "Б": "B",
    "Г": "G",
    "Ц": "C",
    "Ч": "CH",
    "Ш": "SH",
    "Щ": "SCH",
    "Ф": "F",
    "Э": "E",
    "Ю": "YU",
    "Я": "YA",
    "Ж": "ZH",
    "Й": "Y",
}

# Прямой маппинг популярных кириллических префиксов ПК и МФУ
CYRILLIC_PREFIX_ALIASES = {
    # Таблица 1: ПК (головные предприятия)
    "ЗТЕ": "ZTE",
    "ЗТЭ": "ZTE",
    "ЗТЕО": "ZTEO",
    "ЗТЭО": "ZTEO",
    "КЗМ": "KZM",
    "КЗМК": "KZMK",
    "КМК": "KMK",
    "ТЛК": "TLK",
    "ТКТ": "TKT",
    "ТНТ": "TNT",
    "ИТТ": "ITT",
    "ТНМ": "TNM",
    "ГКТ": "GKT",
    # Таблица 2: ПК (филиалы)
    "ЗТ": "ZT",
    "КЗ": "KZ",
    "КМ": "KM",
    "ТЛ": "TL",
    "НТ": "NT",
    "ТТ": "TT",
    "ТМ": "TM",
    "ГК": "GK",
    # Таблица 1 + P: МФУ / Принтеры (головные предприятия)
    "ЗТЕП": "ZTEP",
    "ЗТЭП": "ZTEP",
    "КЗМП": "KZMP",
    "КМКП": "KMKP",
    "ТЛКП": "TLKP",
    "ТКТП": "TKTP",
    "ТНТП": "TNTP",
    "ИТТП": "ITTP",
    "ИТП": "ITTP",
    "ТНМП": "TNMP",
    "ГКТП": "GKTP",
    # Таблица 2 + P: МФУ / Принтеры (филиалы)
    "ЗТП": "ZTP",
    "КЗП": "KZP",
    "КМП": "KMP",
    "ТЛП": "TLP",
    "НТП": "NTP",
    "ТТП": "TTP",
    "ТМП": "TMP",
    "ГКП": "GKP",
    # Общедоменные алиасы
    "НТЕМВ": "NTEMW",
    "НТЕМW": "NTEMW",
    "НТЕМ": "NTEMW",
    "НТЕНВ": "NTEMW",
    "КПК": "KPK",
    "НТЗ": "TKT",
}

# Официальные префиксы ПК (Таблица 1 + Таблица 2 + общедоменные)
MAIN_PC_PREFIXES = ["ZTE", "KZM", "KMK", "TLK", "TKT", "TNT", "ITT", "TNM", "GKT"]
BRANCH_PC_PREFIXES = ["ZT", "KZ", "KM", "TL", "NT", "TT", "TM", "GK"]
DOMAIN_PC_PREFIXES = [
    "NTEMW",
    "KZMK",
    "ZTEO",
    "KPK",
    "NTZ",
    "TEMPO",
    "WKS",
    "PC",
    "SRV",
    "NOTE",
    "LAPTOP",
    "COMP",
]
KNOWN_PC_PREFIXES = MAIN_PC_PREFIXES + BRANCH_PC_PREFIXES + DOMAIN_PC_PREFIXES

# Официальные префиксы МФУ/принтеров (Таблица 1 + 'P' и Таблица 2 + 'P')
MAIN_PRINTER_PREFIXES = [
    "ZTEP",
    "KZMP",
    "KMKP",
    "TLKP",
    "TKTP",
    "TNTP",
    "ITTP",
    "TNMP",
    "GKTP",
]
BRANCH_PRINTER_PREFIXES = ["ZTP", "KZP", "KMP", "TLP", "NTP", "TTP", "TMP", "GKP"]
KNOWN_PRINTER_PREFIXES = MAIN_PRINTER_PREFIXES + BRANCH_PRINTER_PREFIXES

_MAX_PREFIX_EDIT_DISTANCE = 2


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j] + (ca != cb), curr[j] + 1, prev[j + 1] + 1))
        prev = curr
    return prev[-1]


def _split_prefix_and_number(name: str) -> tuple[str, str]:
    cleaned = name.strip()
    m = re.fullmatch(r"([A-Za-zА-Яа-я\-_]+)[\s\-_]*(\d+)", cleaned)
    if m:
        prefix = re.sub(r"[\s\-_]+", "", m.group(1))
        return prefix, m.group(2)
    m_num = re.fullmatch(r"\d+", cleaned)
    if m_num:
        return "", m_num.group(0)
    return cleaned, ""


def _transliterate_prefix(prefix: str) -> str:
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


def _try_fix_prefix(prefix: str, known_prefixes: list[str]) -> str | None:
    if not prefix:
        return None

    clean_p = prefix.upper().replace("-", "").replace("_", "")
    for known in known_prefixes:
        if clean_p == known:
            return known

    best_match = None
    best_dist = _MAX_PREFIX_EDIT_DISTANCE + 1.0
    for known in known_prefixes:
        if len(known) <= 2 and len(clean_p) >= 4:
            continue
        raw_dist = _levenshtein(clean_p, known)
        dist = float(raw_dist)
        if clean_p in known or known in clean_p:
            dist -= 0.1

        if dist < best_dist:
            best_dist = dist
            best_match = known

    max_allowed = 1.0 if len(clean_p) <= 3 else float(_MAX_PREFIX_EDIT_DISTANCE)
    if best_match is not None and best_dist <= max_allowed:
        return best_match
    return None


def _strip_punct(s: str) -> str:
    puncts = {
        ",",
        ";",
        ".",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        "'",
        '"',
        " ",
        "\t",
        "\n",
        "\r",
    }
    while s and s[0] in puncts:
        s = s[1:]
    while s and s[-1] in puncts:
        s = s[:-1]
    return s


def normalize_pc_name(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = _strip_punct(raw)
    if not cleaned or cleaned.lower() in (
        "нет номера",
        "пк",
        "комп",
        "компьютер",
        "ноутбук",
        "wifi",
        "unknown",
        "—",
    ):
        return None
    prefix, number = _split_prefix_and_number(cleaned)
    if not number and not prefix:
        return None
    if not number:
        norm_p = _transliterate_prefix(cleaned)
        fixed_p = _try_fix_prefix(norm_p, KNOWN_PC_PREFIXES)
        return fixed_p or norm_p
    if not prefix:
        return number
    translit_prefix = _transliterate_prefix(prefix)
    fixed_prefix = (
        _try_fix_prefix(translit_prefix, KNOWN_PC_PREFIXES) or translit_prefix
    )
    return f"{fixed_prefix.upper()}{number}"


def normalize_printer_address(raw: str | None) -> str | None:
    """
    Нормализует сетевой адрес или DNS-хостнейм принтера/МФУ:
    1. IP-адреса: исправляет опечатки с запятыми и пробелами (10,244 1.20 -> 10.244.1.20).
    2. DNS-имена: транслитерирует кириллицу и нормализует префикс с добавлением "P" (KZMP, ITTP, TNMP...).
    """
    if not raw:
        return None
    cleaned = _strip_punct(raw)
    if not cleaned:
        return None

    # IP адрес
    ip_match = re.fullmatch(
        r"\d{1,3}[.,\s]+\d{1,3}[.,\s]+\d{1,3}[.,\s]+\d{1,3}", cleaned
    )
    if ip_match:
        return re.sub(r"[.,\s]+", ".", cleaned)

    prefix, number = _split_prefix_and_number(cleaned)
    if not number:
        return cleaned.lower()

    if prefix:
        translit_prefix = _transliterate_prefix(prefix)
        if not translit_prefix.endswith("P") and (
            translit_prefix in MAIN_PC_PREFIXES
            or translit_prefix in BRANCH_PC_PREFIXES
        ):
            translit_prefix = f"{translit_prefix}P"

        fixed_prefix = (
            _try_fix_prefix(translit_prefix, KNOWN_PRINTER_PREFIXES)
            or translit_prefix
        )
        return f"{fixed_prefix.lower()}{number}"

    return cleaned.lower()


def is_valid_pc_name(name: str | None) -> bool:
    if not name:
        return False
    norm = normalize_pc_name(name)
    if not norm or len(norm) < 4:
        return False
    if norm.isdigit() or not any(ch.isdigit() for ch in norm):
        return False
    if norm in ("NTZ-TEMPO", "TEMPO", "KZMK-TEMPO", "ZTE-TEMPO", "ZTEO-TEMPO"):
        return False
    return any(norm.startswith(p) for p in KNOWN_PC_PREFIXES)


def is_valid_printer_name(name: str | None) -> bool:
    if not name:
        return False
    norm = normalize_printer_address(name)
    if not norm or len(norm) < 4:
        return False
    upper = norm.upper()
    return any(upper.startswith(p) for p in KNOWN_PRINTER_PREFIXES)


def resolve_pc_candidates(
    raw_name: str | None, company: str = "", dept: str = ""
) -> list[str]:
    """Генерирует список вероятных хостнеймов ПК для пинга."""
    if not raw_name:
        return []
    cleaned = _strip_punct(raw_name)
    candidates = []
    if not cleaned.isdigit():
        norm = normalize_pc_name(cleaned)
        if norm:
            candidates.append(norm)
            return candidates

    digits = cleaned.zfill(4) if len(cleaned) <= 4 else cleaned
    comp_lower = f"{company} {dept}".lower()

    if any(k in comp_lower for k in ["зтэо", "зтео", "киц"]):
        candidates.extend([f"ZTE{digits}", f"ZT{digits}", f"NTEMW{digits}"])
    elif any(k in comp_lower for k in ["кзмк", "камский завод металло"]):
        candidates.extend(
            [f"KZM{digits}", f"KZ{digits}", f"KZMK{digits}", f"NTEMW{digits}"]
        )
    elif any(k in comp_lower for k in ["кмк", "металлургический"]):
        candidates.extend([f"KMK{digits}", f"KM{digits}", f"NTEMW{digits}"])
    elif any(k in comp_lower for k in ["логистик", "тлк"]):
        candidates.extend(
            [f"TLK{digits}", f"TL{digits}", f"TKT{digits}", f"NTEMW{digits}"]
        )
    elif any(
        k in comp_lower for k in ["трубная", "нтз", "итз", "тд тэм", "трубный"]
    ):
        candidates.extend(
            [f"TKT{digits}", f"NT{digits}", f"NTZ{digits}", f"NTEMW{digits}"]
        )
    elif any(k in comp_lower for k in ["технотрон", "птфк"]):
        candidates.extend([f"TNT{digits}", f"TT{digits}", f"NTEMW{digits}"])
    elif any(k in comp_lower for k in ["метиз", "тнм"]):
        candidates.extend([f"TNM{digits}", f"TM{digits}", f"NTEMW{digits}"])
    elif any(k in comp_lower for k in ["айти", "itt", "it tempo"]):
        candidates.extend([f"ITT{digits}", f"NTEMW{digits}"])
    elif any(k in comp_lower for k in ["группа компаний", "ип ", "гкт"]):
        candidates.extend([f"GKT{digits}", f"GK{digits}", f"NTEMW{digits}"])
    elif any(k in comp_lower for k in ["кпк"]):
        candidates.extend([f"KPK{digits}", f"KMK{digits}", f"NTEMW{digits}"])
    else:
        candidates.extend(
            [
                f"NTEMW{digits}",
                f"TKT{digits}",
                f"KMK{digits}",
                f"KZM{digits}",
                f"TLK{digits}",
            ]
        )

    return list(dict.fromkeys(candidates))


def resolve_printer_candidates(
    raw_name: str | None, company: str = "", dept: str = ""
) -> list[str]:
    """
    Генерирует список вероятных хостнеймов МФУ/принтеров на основе предприятия (с суффиксом "P"):
    - ZTEP / ZTP (ПТФК ЗТЭО)
    - KZMP / KZP (КЗМК ТЭМПО)
    - KMKP / KMP (КМК ТЭМПО)
    - TLKP / TLP (ТЭМПО-Логистик)
    - TKTP / NTP (Трубная компания ТЭМПО)
    - TNTP / TTP (ПТФК Технотрон)
    - ITTP (АЙТИ ТЭМПО)
    - TNMP / TMP (Технотрон-Метиз)
    - GKTP / GKP (Группа компаний ТЭМПО)
    """
    if not raw_name:
        return []
    cleaned = _strip_punct(raw_name)
    candidates = []
    if not cleaned.isdigit():
        norm = normalize_printer_address(cleaned)
        if norm:
            candidates.append(norm)
            return candidates

    digits = cleaned.zfill(4) if len(cleaned) <= 4 else cleaned
    comp_lower = f"{company} {dept}".lower()

    if any(k in comp_lower for k in ["кзмк", "камский завод металло"]):
        candidates.extend([f"kzmp{digits}", f"kzp{digits}", f"ittp{digits}"])
    elif any(
        k in comp_lower for k in ["трубная", "нтз", "итз", "тд тэм", "трубный"]
    ):
        candidates.extend([f"tktp{digits}", f"ntp{digits}", f"ittp{digits}"])
    elif any(k in comp_lower for k in ["кмк", "металлургический"]):
        candidates.extend([f"kmkp{digits}", f"kmp{digits}"])
    elif any(k in comp_lower for k in ["логистик", "тлк"]):
        candidates.extend([f"tlkp{digits}", f"tlp{digits}", f"tktp{digits}"])
    elif any(k in comp_lower for k in ["технотрон", "птфк"]):
        candidates.extend([f"tntp{digits}", f"ttp{digits}"])
    elif any(k in comp_lower for k in ["метиз", "тнм"]):
        candidates.extend([f"tnmp{digits}", f"tmp{digits}"])
    elif any(k in comp_lower for k in ["зтэо", "зтео", "киц"]):
        candidates.extend([f"ztep{digits}", f"ztp{digits}"])
    elif any(k in comp_lower for k in ["айти", "itt"]):
        candidates.append(f"ittp{digits}")
    elif any(k in comp_lower for k in ["группа компаний", "ип ", "гкт"]):
        candidates.extend([f"gktp{digits}", f"gkp{digits}"])
    else:
        candidates.extend(
            [
                f"ittp{digits}",
                f"kzmp{digits}",
                f"kmkp{digits}",
                f"tktp{digits}",
                f"tlkp{digits}",
            ]
        )

    return list(dict.fromkeys(candidates))

