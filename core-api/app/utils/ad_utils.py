"""
Утилиты генерации логинов и извлечения реквизитов пользователей Active Directory.
"""

import re
from typing import Any

from shared.domain import PersonCandidate, validate_person_candidate

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

    surname = (raw_fields.get("1057") or raw_fields.get("1069") or "").strip()
    name = (raw_fields.get("1058") or raw_fields.get("1070") or "").strip()
    patronymic = (raw_fields.get("1059") or raw_fields.get("1071") or "").strip()
    title = (raw_fields.get("1065") or raw_fields.get("1073") or "").strip()
    phone = (raw_fields.get("1066") or raw_fields.get("1075") or "").strip()
    department = (raw_fields.get("1064") or raw_fields.get("1078") or "").strip()
    pc_name = (raw_fields.get("1068") or raw_fields.get("1120") or "").strip()
    company = raw_fields.get("1074", "").strip()
    # Fallback из текста описания
    desc = f"{task.get('Name', '')} {task.get('Description', '')}"
    if not surname or not name:
        m_fio = re.search(
            r"(?:фио|сотрудник|пользователь|работник|ф\.и\.о\.)[:\s]+([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)(?:\s+([А-ЯЁ][а-яё]+))?",
            desc,
            re.IGNORECASE,
        )
        if m_fio:
            cand_surname = m_fio.group(1)
            cand_name = m_fio.group(2)
            cand_patr = m_fio.group(3) or ""
            stop_words = {"учетную", "учетная", "запись", "нового", "пользователя", "пользователь", "доступа", "почту"}
            if cand_surname.lower() not in stop_words and cand_name.lower() not in stop_words:
                surname = surname or cand_surname
                name = name or cand_name
                patronymic = patronymic or cand_patr

    if not title:
        m_title = re.search(r"(?:должность|позиция)[:\s]+([^\n\r,;]+)", desc, re.IGNORECASE)
        if m_title:
            title = m_title.group(1).strip()

    if not department:
        m_dept = re.search(r"(?:подразделение|отдел|департамент)[:\s]+([^\n\r,;]+)", desc, re.IGNORECASE)
        if m_dept:
            department = m_dept.group(1).strip()

    if not company:
        m_comp = re.search(r"(?:организация|компания|юридическое лицо)[:\s]+([^\n\r,;]+)", desc, re.IGNORECASE)
        if m_comp:
            company = m_comp.group(1).strip()

    if not phone:
        m_phone = re.search(r"(?:телефон|тел|внутренний тел)[:\s]+([+\d\s()-]{3,20})", desc, re.IGNORECASE)
        if m_phone:
            phone = m_phone.group(1).strip()

    return {
        "surname": surname,
        "name": name,
        "patronymic": patronymic,
        "title": title,
        "phone": phone or meta.get("phone") or "",
        "department": department or meta.get("department") or "",
        "pc_name": pc_name or meta.get("pc_name") or "",
        "company": company,
    }


def extract_person_candidate_from_task(task: dict[str, Any]) -> PersonCandidate:
    """Return the shared domain representation used by decisioning and workers."""
    return PersonCandidate.model_validate(extract_user_creation_details_from_task(task))


def validate_user_creation_details(task: dict[str, Any]):
    """Validate ticket identity fields without inferring whether a person exists."""
    return validate_person_candidate(extract_person_candidate_from_task(task))
