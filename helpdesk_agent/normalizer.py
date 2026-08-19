"""
Нормализация и извлечение имён сетевых устройств (ПК и принтеров)
на основе проверенной логики из printer-worker.
"""

import logging
import re

logger = logging.getLogger("helpdesk_agent.normalizer")

# Таблица замены кириллических омоглифов на латинские
_CYRILLIC = "ОСАЕРХМТКВ"
_LATIN = "OCAEPXMTKB"
_TR_MAP = str.maketrans(
    _CYRILLIC + _CYRILLIC.lower(),
    _LATIN + _LATIN.lower(),
)

# Корпоративные префиксы рабочих станций и серверов
KNOWN_PC_PREFIXES = [
    "NTEMW", "TKT", "TNT", "KMK", "TNM", "TEMPO", "WKS",
    "PC", "SRV", "NOTE", "LAPTOP", "COMP", "DESKTOP",
    "ZTE", "NTZ", "KZMK",
]

# Корпоративные префиксы сетевых принтеров
KNOWN_PRINTER_PREFIXES = ["ITTP", "KZMP", "KMKP", "ITP"]

_MAX_PREFIX_EDIT_DISTANCE = 2


def _levenshtein(a: str, b: str) -> int:
    """Вычисляет расстояние редактирования (Левенштейна) между строками a и b."""
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
    """Разбивает имя устройства на буквенный префикс и цифровой суффикс."""
    m = re.fullmatch(r"([A-Z\-_]*)(\d+)", name)
    if m:
        return m.group(1).rstrip("-_"), m.group(2)
    return name, ""


def _try_fix_prefix(prefix: str, known_prefixes: list[str]) -> str | None:
    """Пытается исправить опечатку в префиксе по списку известных."""
    if not prefix:
        return None

    clean_p = prefix.upper().replace("-", "").replace("_", "")
    for known in known_prefixes:
        if clean_p == known:
            return known

    best_match: str | None = None
    best_dist = _MAX_PREFIX_EDIT_DISTANCE + 1

    for known in known_prefixes:
        dist = _levenshtein(clean_p, known)
        if dist < best_dist:
            best_dist = dist
            best_match = known

    if best_match is not None and best_dist <= _MAX_PREFIX_EDIT_DISTANCE:
        return best_match
    return None


def normalize_pc_name(raw: str | None) -> str | None:
    """
    Нормализует имя компьютера:
    1. Заменяет кириллические буквы-омоглифы на латинские.
    2. Удаляет лишние пробелы и приводит к верхнему регистру.
    3. Исправляет опечатки в префиксах (NTEMW, TEMPO, ZTE, KZMK, WKS...).
    """
    if not raw:
        return None

    cleaned = raw.strip().strip(",;.()[]{}'\"")
    if not cleaned or cleaned.lower() in ("нет номера", "пк", "комп", "компьютер", "ноутбук", "wifi"):
        return None

    normalized = cleaned.translate(_TR_MAP).replace(" ", "").upper()
    if not normalized:
        return None

    prefix, number = _split_prefix_and_number(normalized)
    if not number:
        # Проверяем прямое совпадение с префиксом (например, TEMPO-WKS)
        for known in KNOWN_PC_PREFIXES:
            if known in normalized:
                return normalized
        return normalized

    if prefix:
        fixed_prefix = _try_fix_prefix(prefix, KNOWN_PC_PREFIXES)
        if fixed_prefix:
            if "-" in prefix:
                return f"{fixed_prefix}-{number}"
            return f"{fixed_prefix}{number}"

    return normalized


def is_valid_pc_name(name: str | None) -> bool:
    """Проверяет, является ли строка корректным именем рабочего ПК."""
    if not name:
        return False
    norm = normalize_pc_name(name)
    if not norm or len(norm) < 4:
        return False
    # Имя ПК не может быть чистым числом и обязано содержать цифры
    if norm.isdigit() or not any(ch.isdigit() for ch in norm):
        return False
    # Исключаем названия доменов, юрлиц и заводов
    if norm in ("NTZ-TEMPO", "TEMPO", "KZMK-TEMPO", "ZTE-TEMPO", "ZTEO-TEMPO"):
        return False
    return any(p in norm for p in KNOWN_PC_PREFIXES)
