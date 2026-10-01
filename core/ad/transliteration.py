"""Transliteration and sAMAccountName generation utilities (GOST 7.79-2000 System B)."""

import re
from typing import Optional

# GOST 7.79-2000 (System B) transliteration table for Cyrillic -> Latin
GOST_779_SYSTEM_B_MAP = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def transliterate_to_latin(text: str) -> str:
    """Transliterate Russian Cyrillic string to clean Latin according to GOST 7.79-2000."""
    if not text:
        return ""
    result = []
    for char in text.lower():
        result.append(GOST_779_SYSTEM_B_MAP.get(char, char))
    return "".join(result)


def generate_sam_account_name(
    surname: str,
    name: str,
    patronymic: Optional[str] = None,
    collision_index: int = 1,
) -> str:
    """Generate canonical Active Directory sAMAccountName with collision index support.

    Convention:
      - Base (with patronymic): {surname_lat}.{name_initial_lat}.{patronymic_initial_lat} (e.g., ivanov.i.i)
      - Fallback (no patronymic): {surname_lat}.{name_initial_lat} (e.g., ivanov.i)
      - Collision (index > 1): {base}{collision_index} (e.g., ivanov.i.i2, ivanov.i2)
      - Strict constraint: Active Directory sAMAccountName must not exceed 20 characters.
    """
    s_lat = re.sub(r"[^a-z0-9]", "", transliterate_to_latin(surname.strip()))
    n_lat = re.sub(r"[^a-z0-9]", "", transliterate_to_latin(name.strip()))
    p_lat = re.sub(r"[^a-z0-9]", "", transliterate_to_latin((patronymic or "").strip()))

    n_init = n_lat[0] if n_lat else ""
    p_init = p_lat[0] if p_lat else ""

    suffix = str(collision_index) if collision_index > 1 else ""

    if n_init and p_init:
        # Format: surname.n.n or surname.n.n2
        base_suffix = f".{n_init}.{p_init}{suffix}"
        max_surname_len = max(1, 20 - len(base_suffix))
        sam = f"{s_lat[:max_surname_len]}{base_suffix}"
    elif n_init:
        # Format: surname.n or surname.n2 (when patronymic is missing)
        base_suffix = f".{n_init}{suffix}"
        max_surname_len = max(1, 20 - len(base_suffix))
        sam = f"{s_lat[:max_surname_len]}{base_suffix}"
    else:
        max_surname_len = max(1, 20 - len(suffix))
        sam = f"{s_lat[:max_surname_len]}{suffix}"

    return sam[:20].lower()
