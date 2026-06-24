"""
Нормализация имён сетевых устройств (ПК и МФУ/принтеров).

Содержит надежные, детерминированные механизмы:
1. Замена кириллических омоглифов на латинские.
2. Нечёткое исправление префиксов по расстоянию Левенштейна:
   - Для ПК (NTEMW, TKT, TNT, KMK, TNT, TNM)
   - Для принтеров/МФУ (ITTP, KZMP, KMKP)
3. Приведение к верхнему регистру, удаление лишних пробелов.
"""

import re
import logging

logger = logging.getLogger(__name__)

# Таблица замены кириллических омоглифов на латинские
_CYRILLIC = "ОСАЕРХМТКВ"
_LATIN    = "OCAEPXMTKB"
_TR_MAP = str.maketrans(
    _CYRILLIC + _CYRILLIC.lower(),
    _LATIN    + _LATIN.lower(),
)

# Известные префиксы ПК
KNOWN_PC_PREFIXES = ["NTEMW", "TKT", "TNT", "KMK", "TNM"]

# Известные префиксы принтеров
KNOWN_PRINTER_PREFIXES = ["ITTP", "KZMP", "KMKP"]

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
    """
    Разбивает имя устройства на буквенный префикс и цифровой суффикс.
    Например: 'ITP0001' -> ('ITP', '0001')
    """
    m = re.fullmatch(r"([A-Z]*)(\d+)", name)
    if m:
        return m.group(1), m.group(2)
    return name, ""


def _try_fix_prefix(prefix: str, known_prefixes: list[str]) -> str | None:
    """Пытается исправить опечатку в префиксе, сравнивая со списком известных."""
    if not prefix:
        return None

    best_match: str | None = None
    best_dist = _MAX_PREFIX_EDIT_DISTANCE + 1

    for known in known_prefixes:
        dist = _levenshtein(prefix, known)
        if dist < best_dist:
            best_dist = dist
            best_match = known

    if best_match is not None and best_dist <= _MAX_PREFIX_EDIT_DISTANCE:
        return best_match
    return None


def normalize_pc_name(raw: str | None) -> str | None:
    """Нормализует имя компьютера (транслитерация + исправление опечаток в префиксе)."""
    if not raw:
        return None

    normalized = raw.translate(_TR_MAP).replace(" ", "").upper()
    if not normalized:
        return None

    prefix, number = _split_prefix_and_number(normalized)
    if not number:
        return normalized

    if prefix:
        fixed_prefix = _try_fix_prefix(prefix, KNOWN_PC_PREFIXES)
        if fixed_prefix and fixed_prefix != prefix:
            corrected = fixed_prefix + number
            logger.info("Нормализация префикса ПК: '%s' -> '%s'", normalized, corrected)
            return corrected

    return normalized


def normalize_printer_address(raw: str | None) -> str | None:
    """
    Нормализует сетевой адрес принтера:
    - Если это IP-адрес, возвращает как есть (с заменой опечаток вроде запятых).
    - Если это DNS-хостнейм, выполняет транслитерацию и пытается исправить префикс (например itp -> ittp).
    """
    if not raw:
        return None

    # Очистка опечаток в IP-адресах (запятые/пробелы вместо точек)
    ip_match = re.fullmatch(r"\d{1,3}[., ]+\d{1,3}[., ]+\d{1,3}[., ]+\d{1,3}", raw.strip())
    if ip_match:
        return re.sub(r"[., ]+", ".", raw.strip())

    # DNS-имя: приводим к верхнему регистру и транслитерируем омоглифы
    normalized = raw.translate(_TR_MAP).replace(" ", "").upper()
    if not normalized:
        return None

    prefix, number = _split_prefix_and_number(normalized)
    if not number:
        return normalized

    if prefix:
        fixed_prefix = _try_fix_prefix(prefix, KNOWN_PRINTER_PREFIXES)
        if fixed_prefix and fixed_prefix != prefix:
            corrected = fixed_prefix + number
            logger.info("Нормализация префикса принтера: '%s' -> '%s'", normalized, corrected)
            return corrected.lower()  # Имя принтера обычно в нижнем регистре

    return normalized.lower()
