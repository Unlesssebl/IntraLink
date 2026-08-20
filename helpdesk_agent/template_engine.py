import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("helpdesk_agent.template_engine")

TEMPLATES_FILE = os.path.join(os.path.dirname(__file__), "templates.json")

# Справочник корневых сервисов каталога IntraService
ROOT_SERVICES: dict[str, dict[str, Any]] = {
    "01": {"id": 42, "name": "01. Учетные записи пользователей"},
    "02": {"id": 18, "name": "02. Установка и настройка программ"},
    "03": {"id": 19, "name": "03. Установка и обслуживание оргтехники"},
    "04": {"id": 20, "name": "04. Проблемы с сетью и интернетом"},
    "05": {"id": 23, "name": "05. Вопросы по DIRECTUM, B2B"},
    "06": {"id": 15, "name": "06. Вопросы по 1С"},
    "07": {"id": 60, "name": "07. Вопросы по терминалу сбора данных"},
    "08": {"id": 72, "name": "08. Информационная безопасность"},
    "09": {"id": 24, "name": "09. Электронная цифровая подпись"},
    "10": {"id": 64, "name": "10. Телефония"},
    "11": {"id": 16, "name": "11. Общие вопросы"},
    "12": {"id": 125, "name": "12. Архив КТД"},
    "14": {"id": 136, "name": "14. MDC системы"},
    "15": {"id": 144, "name": "15. Вопросы по HelpDesk"},
    "16": {"id": 188, "name": "16. Вопросы по IPS PDM\\PLM"},
}

# Маппинг всех известных ID подразделов к их корневому разделу (01..16)
SERVICE_ID_TO_ROOT: dict[int, str] = {
    # 01. Учетные записи
    42: "01", 53: "01", 63: "01", 54: "01", 55: "01", 124: "01", 104: "01", 186: "01",
    # 02. Установка и настройка ПО
    18: "02", 58: "02", 59: "02", 149: "02",
    # 03. Оргтехника и ПК
    19: "03", 32: "03", 33: "03", 183: "03", 184: "03", 150: "03", 57: "03",
    # 04. Проблемы с сетью
    20: "04", 43: "04", 71: "04", 181: "04",
    # 05. Directum / B2B
    23: "05", 41: "05", 40: "05",
    # 06. Вопросы по 1С
    15: "06", 28: "06", 50: "06", 51: "06", 26: "06", 45: "06", 46: "06", 47: "06", 48: "06",
    52: "06", 27: "06", 29: "06", 39: "06", 185: "06",
    # 07. ТСД
    60: "07", 61: "07", 62: "07", 182: "07",
    # 08. ИБ
    72: "08", 56: "08", 74: "08", 73: "08", 38: "08", 36: "08", 105: "08", 128: "08", 130: "08", 129: "08", 75: "08",
    # 09. ЭЦП
    24: "09", 30: "09", 31: "09",
    # 10. Телефония
    64: "10", 65: "10", 66: "10", 67: "10", 68: "10", 69: "10", 70: "10",
    # 11. Общие вопросы
    16: "11", 106: "11",
    # 12. Архив КТД
    125: "12", 126: "12",
    # 14. MDC системы
    136: "14", 132: "14", 137: "14", 133: "14", 139: "14",
    # 15. HelpDesk
    144: "15", 151: "15", 127: "15",
    # 16. IPS PDM/PLM
    188: "16",
}


def get_root_number_for_service_id(service_id: int | None) -> str | None:
    """Возвращает номер корневого раздела ('01'..'16') для заданного ServiceId."""
    if service_id is None:
        return None
    return SERVICE_ID_TO_ROOT.get(int(service_id))


def get_root_name(root_num: str) -> str:
    """Возвращает человекочитаемое имя корневого раздела."""
    return ROOT_SERVICES.get(root_num, {}).get("name", f"Раздел {root_num}")


def load_templates() -> dict[str, dict[str, Any]]:
    """Загружает шаблоны из templates.json."""
    if os.path.exists(TEMPLATES_FILE):
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Ошибка загрузки templates.json: %s", e)
    return {}


def render_template(template_key: str, context: dict[str, Any]) -> dict[str, Any]:
    """
    Подставляет переменные контекста в указанный шаблон.
    """
    templates = load_templates()
    tmpl = templates.get(template_key) or templates.get("in_work_standard", {
        "name": "Стандартное принятие в работу",
        "status_id": 27,
        "status_name": "В работе",
        "expenses": 10,
        "template": "Добрый день! Ваша заявка принята в работу. По вопросам звоните на номер 49-87.",
    })

    raw_text = tmpl.get("template", "")
    pc_name = context.get("pc_name") or "ПК"
    room = context.get("room") or "кабинет"
    phone = context.get("phone") or "49-87"
    target_service = context.get("target_service") or "соответствующем разделе каталога"
    occupied_user = context.get("occupied_user") or "другой сотрудник"
    details = context.get("details") or "удобное время"

    rendered_text = raw_text.replace("{pc_name}", pc_name)
    rendered_text = rendered_text.replace("{room}", room)
    rendered_text = rendered_text.replace("{phone}", phone)
    rendered_text = rendered_text.replace("{target_service}", target_service)
    rendered_text = rendered_text.replace("{occupied_user}", occupied_user)
    rendered_text = rendered_text.replace("{details}", details)

    return {
        "template_key": template_key,
        "name": tmpl.get("name"),
        "status_id": tmpl.get("status_id", 27),
        "status_name": tmpl.get("status_name", "В работе"),
        "expenses": tmpl.get("expenses", 10),
        "comment": rendered_text.strip(),
    }


def classify_target_service(text: str, current_service_id: int | None = None) -> tuple[str | None, str | None]:
    """
    Семантическая классификация текста инцидента в целевой раздел каталога IntraService.
    Возвращает (root_num, match_reason), например ('06', 'обнаружены ключевые слова 1С:УПП/ERP').
    """
    t = text.lower()

    # 1. Вопросы по DIRECTUM, B2B (05)
    if any(w in t for w in ["directum", "директум", "договор 6/", "согласование договора", "карточка договора"]) or re.search(r"\bb2b\b|\bб2б\b", t):
        return "05", "вопросы документооборота Directum / площадок B2B"
    if "контрагент" in t and any(w in t for w in ["договор", "прикрепи", "данные", "карточк"]):
        return "05", "данные контрагентов и карточек в Directum"

    # Проверка на общие системные проблемы производительности ПК (1-я линия, не 1С)
    is_general_pc_lag = any(w in t for w in [
        "компьютер работает медленно", "медленно работает сам компьютер",
        "тормозит компьютер", "зависает компьютер", "зависает пк", "зависает весь пк",
        "принт скрин не", "скриншот не", "не сохраняет скрин", "не сохраняет выделенный",
        "зависает весь", "виснет пк", "виснет компьютер", "невозможно работать,тормозит"
    ])

    # 2. Вопросы по 1С (06)
    if not is_general_pc_lag:
        if re.search(r"\b1[сc]\b|\bупп\b|\berp\b|\bзуп\b", t) or any(w in t for w in [
            "бухгалтери", "документооборот 1с", "база 1с", "кэш 1с",
            "не включается 1с", "вылетает 1с", "ошибка 1с", "заблокирована таблица", "база данных 1с",
            "провести документ", "не проводится", "печатная форма", "макет 1с", "открытие периода", "номенклатур", "штрихкод в 1с"
        ]):
            return "06", "инцидент или ошибка в информационной базе 1С"
        if "кэш 1с" in t or "кэш базы" in t or ("кэш" in t and any(w in t for w in ["1с", "1c", "баз", "упп", "зуп", "erp"])):
            return "06", "очистка кэша базы 1С"

    # Проверка на зависшую блокировку файла (SMB-сессия / занят другим пользователем - 1 линия, не ИБ)
    is_file_lock = any(w in t for w in ["занят другим", "занята другим", "заблокирован другим", "не впускает", "якобы я им пользуюсь", "кем-то занят"])

    # 3. Информационная безопасность / Доступ к сетевым папкам / USB-накопители / PERCO / Антивирус (08)
    is_usb_storage = any(w in t for w in ["разблокировка usb", "разблокировать usb", "заблокирован usb", "доступ к usb", "флешк", "съемный накопитель"])
    is_usb_peripheral = any(w in t for w in ["usb принтер", "принтер usb", "принтер (usb)", "сканер usb", "мышь usb", "клавиатура usb", "кабель usb"])

    if not is_file_lock and (
        any(w in t for w in [
            "доступ в папку", "доступ к папке", "доступ к обменник", "обменник", "папку брак", "папка брак",
            "папка отдела", "сетевая папка", "сетевой диск", "права на папку", "доступ на добавление файлов",
            "perco", "перко", "пропуск", "турникет",
            "скуд", "видеонаблюдение", "камера", "внешний доступ", "удаленный доступ", "отчет по удаленке",
            "доступ к файлам уволенного", "антивирус", "kaspersky", "касперский", "обновление антивируса"
        ]) or is_usb_storage or (re.search(r"\bvpn\b|\bвпн\b", t))
    ):
        return "08", "разграничение прав доступа к сетевым ресурсам / ИБ / USB / СКУД / Антивирус"
    if not is_file_lock and "обменный" in t and any(w in t for w in ["файл", "папк", "диск"]):
        return "08", "доступ к сетевым обменным файлам и папкам"

    # 4. Телефония (10)
    if any(w in t for w in [
        "телефони", "стационарный телефон", "телефонная связь", "не работает телефон",
        "переадресация", "подключение телефона", "корректировка фио на телефоне", "распределение вызовов",
        "внутренний телефон", "настройка телефона", "avaya", "ip-телефон", "ip телефон", "sip", "трубк"
    ]) or (re.search(r"\bтелефон\b|\bтелефона\b|\bтелефону\b", t) and not any(w in t for w in ["позвони", "звони", "связаться", "контакт", "для связи", "номер телефона", "номер:"])):
        return "10", "настройка, подключение или сбой телефонной связи"

    # 5. Электронная цифровая подпись (09)
    if re.search(r"\bэцп\b", t) or any(w in t for w in [
        "криптопро", "cryptopro", "сертификат эцп", "банк-клиент", "сбербанк", "втб",
        "сбис", "госуслуги", "рутокен", "rutoken", "электронная подпись", "контур"
    ]):
        return "09", "настройка или продление сертификатов ЭЦП"

    # 6. Терминалы сбора данных ТСД (07)
    if re.search(r"\bтсд\b|\btsd\b", t) or any(w in t for w in ["терминал сбора данных", "cipherlab", "urovo", "utech", "связь с базой тсд"]):
        return "07", "настройка и связь с базой терминалов ТСД"

    # 7. MDC системы (14)
    if (re.search(r"\bmdc\b|\bмдс\b|\bdpa\b", t) or "аис диспетчер" in t or
        ("диспетчер" in t and not any(w in t for w in ["диспетчер лицензий", "диспетчер задач", "диспетчер устройств", "autocad", "компас", "solidworks"]))):
        return "14", "системы сбора машинных данных MDC (АИС Диспетчер, DPA)"

    # 8. IPS PDM/PLM (16)
    if re.search(r"\bips\b|\bpdm\b|\bplm\b|\bипс\b", t) or any(w in t for w in ["интермех", "intermech"]):
        return "16", "конструкторско-технологическая система IPS PDM/PLM"

    # 9. Архив КТД (12)
    if any(w in t for w in ["архив ктд", "ктд", "кд и тд", "конструкторская документация"]):
        return "12", "централизованный архив КТД"

    # Исключение для ПО: если явно запрашивается установка/настройка прикладной программы
    is_software_install_request = any(w in t for w in [
        "установка программ", "установить программ", "установка софта", "установить конвертер",
        "установка конвертера", "установить программу", "установка finereader", "установить браузер",
        "установить office", "установить word", "установить excel", "установить акробат"
    ])

    # 10. Оргтехника, принтеры, ПК, аппаратный ремонт, общие тормоза (03)
    if not is_software_install_request and (
        is_general_pc_lag or is_usb_peripheral or any(w in t for w in [
            "принтер", "мфу", "картридж", "тонер", "замятие бумаги",
            "kyocera", "ecosys", "hp laserjet", "canon", "xerox", "принтер этикеток", "zebra", "godex",
            "не работает комп", "не включается компьютер", "системный блок", "весы кпп", "весовое",
            "периферия", "акт экспертизы", "списание монитора", "списание пк",
            "замена диска", "замена hdd", "замена ssd", "черный экран", "пищит", "шумит", "греется"
        ]) or (
            any(w in t for w in ["сканер", "не сканирует", "печать"]) and not any(w in t for w in ["1с", "упп", "erp", "конвертер"])
        )
    ):
        return "03", "оргтехника, принтеры или обслуживание/производительность ПК"

    # 11. Проблемы с сетью и интернетом / Wi-Fi (04)
    if any(w in t for w in [
        "нет интернета", "не работает интернет", "сетевой кабель", "патч-корд", "обрыв сети", "монтаж сети",
        "wi-fi", "wifi", "вайфай", "вай-фай", "work-net", "пароль от wi-fi", "нет сети"
    ]):
        return "04", "сетевые подключения, монтаж ЛВС или доступ к Wi-Fi"

    # 12. Установка и настройка программ (02)
    is_remote_support_issue = any(w in t for w in ["нет соединения", "не подключается", "сбой anydesk", "ошибка anydesk", "не могу подключиться"])
    if is_software_install_request or (
        not is_remote_support_issue and any(w in t for w in [
            "активация office", "активация windows", "акробат", "acrobat",
            "pdf reader", "winrar", "7-zip", "браузер", "chrome", "yandex", "wechat", "компас", "autocad",
            "обновить акробат", "установить ос", "новая версия ос"
        ])
    ):
        return "02", "установка, обновление или настройка прикладного ПО"

    # 13. Учетные записи пользователей (01)
    if any(w in t for w in [
        "создание нового пользователя", "создать учетную запись", "создать почту", "заблокировать пользователя",
        "увольнение", "смена фамилии", "изменение данных сотрудника", "заявка на изменение учетных данных"
    ]):
        return "01", "учетные записи, почтовые ящики и учетные данные сотрудников"

    return None, None


def detect_service_redirect(task: dict[str, Any]) -> dict[str, Any] | None:
    """
    Проверяет, требует ли заявка отмены и редиректа в другой раздел каталога.
    Если обнаружено несоответствие разделов, возвращает dict с подробным описанием редиректа.
    Если заявка подана корректно, возвращает None.
    """
    service_id = task.get("ServiceId")
    current_root = get_root_number_for_service_id(service_id)
    current_service_name = task.get("ServiceName") or (get_root_name(current_root) if current_root else "Неизвестный раздел")

    name = task.get("Name") or ""
    desc = task.get("Description") or ""
    full_text = f"{name}. {desc}".strip()

    target_root, reason = classify_target_service(full_text, service_id)
    if not target_root:
        return None

    # Если раздел определен и он отличается от текущего корневого раздела
    # (или если текущий раздел - "11. Общие вопросы", из которого всегда требуется редирект)
    if current_root != target_root or current_root == "11":
        # Исключение: создание пользователя 1С (ID 54) или Directum (ID 55) в разделе 01 является штатным
        if current_root == "01" and service_id in (54, 55, 186):
            if any(w in full_text.lower() for w in ["создать", "создание", "новый пользователь", "учетная запись"]):
                return None

        # Исключение: установка ПО на ноутбук/ПК в разделе 02 является штатной
        if current_root == "02" and any(w in full_text.lower() for w in ["установка", "установить", "настройка программ", "программ"]) and not any(w in full_text.lower() for w in ["картридж", "ремонт", "замятие", "весы"]):
            return None

        # Исключение: установка 1С (ID 185) внутри раздела 06
        if current_root == "06" and target_root == "02" and "1с" in full_text.lower():
            return None

        # Исключение: вопросы по HelpDesk (15) не редиректить в 02 ПО из-за упоминания браузера
        if current_root == "15" and any(w in full_text.lower() for w in ["helpdesk", "хелпдеск", "интрасервис", "браузер", "вкладк"]):
            return None

        # Исключение: AnyDesk / Ассистент в разделе 04 Сеть
        if current_root == "04" and any(w in full_text.lower() for w in ["anydesk", "ассистент"]):
            return None

        # Исключение: подраздел 181 (Wi-Fi) внутри 04
        if service_id == 181 and target_root == "04":
            return None

        target_name = get_root_name(target_root)
        current_name = get_root_name(current_root) if current_root else current_service_name

        comment = (
            f"Заявка отменена т.к. создана не в правильном разделе.\n"
            f"Требуется оставить заявку в подходящем разделе: {target_name}.\n"
            f"По вопросам звоните на номер 49-87."
        )

        return {
            "is_redirect": True,
            "current_root": current_root,
            "current_service_name": current_name,
            "target_root": target_root,
            "target_service_name": target_name,
            "reason": reason,
            "template_key": "wrong_service",
            "name": "Неверный раздел каталога услуг",
            "status_id": 30,
            "status_name": "Отменена",
            "expenses": 5,
            "comment": comment,
        }

    return None


def auto_detect_template(
    task: dict[str, Any],
    diag: dict[str, Any] | None = None,
    kb_matches: list[dict[str, Any]] | None = None,
    redirect_mode: bool = False,
) -> dict[str, Any]:
    """
    Интеллектуальный авто-подбор наиболее точного шаблона на основе контекста инцидента.
    """
    # 0. Проверка на редирект в другой раздел каталога
    redirect_info = detect_service_redirect(task)
    if redirect_info:
        return redirect_info

    # 0.5. Семантический RAG-консенсус (проверенные исторические решения базы знаний при сходстве >= 90%)
    name = (task.get("Name") or task.get("name") or "").lower()
    desc = (task.get("Description") or task.get("description") or "").lower()
    service_name = (task.get("ServiceName") or task.get("service_name") or "").lower()
    user_text = f"{name} {desc}".strip()

    if kb_matches and not redirect_mode:
        top_kb = kb_matches[0]
        sim = float(top_kb.get("similarity_pct", 0))
        sol = (top_kb.get("solution") or "").strip()
        status_name = top_kb.get("status_name", "")
        # Автоматическое выполнение (29) разрешено ТОЛЬКО для чистых запросов на обслуживание/выдачу доступов (Wi-Fi, почта, инструкция),
        # но ЗАПРЕЩЕНО для неисправностей (принтеры, сбои, тормоза, ошибки)
        is_troubleshooting_incident = any(w in user_text for w in [
            "не печатает", "не работает", "ошибка", "сбой", "тормозит", "зависает", "вылетает", "не сканирует", "не включается", "проблема"
        ])
        if sim >= 90.0 and sol and len(sol) >= 15:
            if "выполнен" in status_name.lower() and not is_troubleshooting_incident:
                return {
                    "template_key": "rag_historical_solution",
                    "name": f"🧠 Решение базы знаний (#{top_kb.get('task_id')}, сходство {sim}%)",
                    "status_id": 29,
                    "status_name": "Выполнена",
                    "expenses": 10,
                    "comment": sol,
                    "rag_applied": True,
                    "rag_task_id": top_kb.get("task_id"),
                    "rag_similarity": sim,
                }
    
    meta = task.get("_field_meta") or {}
    pc_name = meta.get("pc_name") or ""
    room = meta.get("room") or ""
    phone = meta.get("phone") or ""

    context = {
        "pc_name": pc_name or "ПК",
        "room": room,
        "phone": phone,
        "target_service": "Общий раздел",
    }

    # 1. Приоритет физической доставки техники в каб. 112 (Статус 48)
    is_physical_delivery = any(w in user_text for w in [
        "принесу к вам", "привезем", "принесем", "принесу компьютер", "принести компьютер",
        "принести системный", "принести в 112", "принести устройство", "принесу ноутбук"
    ])
    if is_physical_delivery:
        return render_template("bring_pc_112" if any(w in user_text for w in ["пк", "компьютер", "системн", "блок", "ноутбук"]) else "bring_device_112", context)

    # 2. Wi-Fi доступ (ТОЛЬКО если заявитель явно запросил Wi-Fi в тексте инцидента, а не просто выбрал пункт меню)
    is_wifi_request = any(w in user_text for w in ["wi-fi", "wifi", "вайфай", "вай-фай", "work-net", "пароль от сети", "пароль от wi-fi", "доступ к wi-fi"])
    if is_wifi_request and not any(w in user_text for w in ["excel", "exle", "обменник", "папк", "диск", "1с", "принтер"]):
        return render_template("wifi_access", context)

    # 3. Создание электронной почты (раздел 01)
    if "почт" in user_text and any(w in user_text for w in ["создать почту", "создание почты", "электронная почта", "новый ящик"]):
        return render_template("email_created", context)

    # 4. AnyDesk сбой / переход на Ассистент
    if any(w in user_text for w in ["anydesk не", "не подключается anydesk", "ошибка anydesk", "не могу подключиться через anydesk"]):
        return render_template("anydesk_fallback_assistant", context)

    # 5. Аппаратный ремонт / Системный блок / Доставка в 112 каб
    if any(w in user_text for w in [
        "диагностика пк", "новый процессор", "новый системный", "новый пк", "замена диска",
        "замена hdd", "замена ssd", "черный экран", "пищит компьютер", "замена памяти",
        "аппаратный ремонт", "сгорел", "задымился", "второй монитор", "видеокарт"
    ]):
        return render_template("bring_device_112", context)

    # 6. Проверка на списание или групповой монтаж (НЕ слать pc_offline)
    is_decommission_or_event = any(w in user_text for w in [
        "списание", "списать", "дефектовк", "акт о неисправности", "конференц", "обучение",
        "подключить 6 компьютеров", "подключить компьютеры к сети"
    ])

    # 7. Принтеры и оргтехника (МФУ не в сети или уточнение IP)
    if any(w in user_text for w in ["принтер", "мфу", "kyocera", "ecosys", "hp laserjet", "canon", "xerox"]):
        if diag and not diag.get("is_online", False) and diag.get("target"):
            return render_template("printer_offline", context)
        if any(w in user_text for w in ["ip принтера", "укажите ip", "не печатает принтер", "подключить принтер"]):
            return render_template("printer_ip_clarify", context)

    # 8. ПК не в сети (если хост явно найден, оффлайн и это не списание/монтаж)
    if not is_decommission_or_event and diag and not diag.get("is_online", False) and diag.get("target") and diag.get("target") != "UNKNOWN":
        context["pc_name"] = diag.get("target")
        return render_template("pc_offline", context)

    # 9. Fallback на стандартное принятие в работу
    return render_template("in_work_standard", context)

