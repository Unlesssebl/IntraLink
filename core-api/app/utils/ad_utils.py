"""
Утилиты генерации логинов и извлечения реквизитов пользователей Active Directory.
"""

import re
from typing import Any

# Таблица транслитерации ГОСТ 7.79-2000 (система Б)
TRANSLIT_TABLE = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
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
    """Транслитерирует русский текст в латиницу по стандарту корпоративных логинов."""
    res = []
    for char in text.lower():
        res.append(TRANSLIT_TABLE.get(char, char))
    return "".join(res)


def generate_sam_account_name(
    surname: str, name: str, patronymic: str | None = None
) -> str:
    """
    Генерирует стандартный sAMAccountName в формате:
    Фамилия + первая буква имени (например, Иванов Иван -> ivanov.i или ivanovi).
    В корпоративном стандарте IntraLink: surname + '.' + name[0] + ('.' + patronymic[0] if present)
    или surname_initials: ivanov.i.i
    """
    s_lat = transliterate_to_latin(surname.strip())
    n_lat = transliterate_to_latin(name.strip())
    n_init = n_lat[0] if n_lat else ""

    p_init = ""
    if patronymic and patronymic.strip():
        p_lat = transliterate_to_latin(patronymic.strip())
        p_init = p_lat[0] if p_lat else ""

    if p_init:
        base = f"{s_lat}.{n_init}.{p_init}"
    elif n_init:
        base = f"{s_lat}.{n_init}"
    else:
        base = s_lat

    clean = re.sub(r"[^a-z0-9.]", "", base.lower())
    return clean[:20]


def extract_user_creation_details_from_task(
    task: dict[str, Any],
) -> dict[str, Any]:
    """
    Извлекает реквизиты сотрудника (Фамилия, Имя, Отчество, Подразделение, Телефон и т.д.)
    из кастомных полей или текста заявки.
    """
    meta = task.get("_field_meta") or {}
    raw_fields = meta.get("raw") or {}

    surname = raw_fields.get("1069", "").strip()
    name = raw_fields.get("1070", "").strip()
    patronymic = raw_fields.get("1071", "").strip()
    title = raw_fields.get("1073", "").strip()
    phone = raw_fields.get("1075", "").strip()
    department = raw_fields.get("1078", "").strip()
    pc_name = raw_fields.get("1120", "").strip()
    company = raw_fields.get("1074", "").strip()

    # Fallback из текста описания
    if not surname or not name:
        desc = f"{task.get('Name', '')} {task.get('Description', '')}"
        m_fio = re.search(
            r"(?:фио|сотрудник|пользователь|создать)[:\s]+([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)(?:\s+([А-ЯЁ][а-яё]+))?",
            desc,
            re.IGNORECASE,
        )
        if m_fio:
            surname = surname or m_fio.group(1)
            name = name or m_fio.group(2)
            patronymic = patronymic or (m_fio.group(3) or "")

    return {
        "surname": surname,
        "name": name,
        "patronymic": patronymic,
        "title": title,
        "phone": phone or meta.get("phone"),
        "department": department or meta.get("department"),
        "pc_name": pc_name or meta.get("pc_name"),
        "company": company,
    }
