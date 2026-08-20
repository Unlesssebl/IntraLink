import re
from typing import Any

try:
    from .base import BaseRule, RuleDecision
    from .catalog import get_root_name, get_root_number_for_service_id
except (ImportError, ValueError):
    from rules.base import BaseRule, RuleDecision
    from rules.catalog import get_root_name, get_root_number_for_service_id


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

    # 3. Информационная безопасность / Сброс паролей / Доступ к папкам / USB / PERCO / Антивирус (08)
    is_usb_storage = any(w in t for w in ["разблокировка usb", "разблокировать usb", "заблокирован usb", "доступ к usb", "флешк", "съемный накопитель"])
    is_usb_peripheral = any(w in t for w in ["usb принтер", "принтер usb", "принтер (usb)", "сканер usb", "мышь usb", "клавиатура usb", "кабель usb"])

    is_password_security = any(w in t for w in [
        "не помню пароль", "забыла пароль", "забыл пароль", "сбросить пароль", "сброс пароля",
        "проблема со входом в ноутбук", "проблема со входом в компьютер", "не могу войти в ноутбук",
        "не могу войти в компьютер", "заблокирован пароль", "заблокирована учетная запись",
        "разблокировать учетную", "разблокировка учетной", "пароль от учетной записи", "пароль учетной записи"
    ])

    if is_password_security:
        return "08", "сброс/восстановление пароля учетной записи или разблокировка входа (ИБ)"

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


class ServiceRedirectRule(BaseRule):
    """
    Правило 0: Проверка соответствия раздела каталога (Wrong Service -> Редирект в Статус 30).
    """

    def __init__(self, priority: int = 10):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "ServiceRedirectRule"

    def evaluate(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> RuleDecision | None:
        service_id = task.get("ServiceId")
        current_root = get_root_number_for_service_id(service_id)
        current_service_name = task.get("ServiceName") or (get_root_name(current_root) if current_root else "Неизвестный раздел")

        name = task.get("Name") or ""
        desc = task.get("Description") or ""
        full_text = f"{name}. {desc}".strip()

        target_root, reason = classify_target_service(full_text, service_id)
        if not target_root:
            return None

        if current_root != target_root or current_root == "11":
            # Исключения, когда перенаправление не требуется:
            if current_root == "01" and service_id in (54, 55, 186):
                if any(w in full_text.lower() for w in ["создать", "создание", "новый пользователь", "учетная запись"]):
                    return None

            if current_root == "02" and any(w in full_text.lower() for w in ["установка", "установить", "настройка программ", "программ"]) and not any(w in full_text.lower() for w in ["картридж", "ремонт", "замятие", "весы"]):
                return None

            if current_root == "06" and target_root == "02" and "1с" in full_text.lower():
                return None

            if current_root == "15" and any(w in full_text.lower() for w in ["helpdesk", "хелпдеск", "интрасервис", "браузер", "вкладк"]):
                return None

            if current_root == "04" and any(w in full_text.lower() for w in ["anydesk", "ассистент"]):
                return None

            if service_id == 181 and target_root == "04":
                return None

            target_name = get_root_name(target_root)
            current_name = get_root_name(current_root) if current_root else current_service_name

            comment = (
                f"Заявка отменена, т. к. создана не в подходящем разделе.\n"
                f"Требуется оставить заявку в подходящем разделе: {target_name}.\n"
                f"Если у вас остались вопросы, пожалуйста, напишите в комментариях к этой заявке."
            )

            return RuleDecision(
                template_key="wrong_service",
                name="Неверный раздел каталога услуг",
                status_id=30,
                status_name="Отменена",
                expenses=5,
                comment=comment,
                is_redirect=True,
                current_root=current_root,
                target_root=target_root,
                target_service_name=target_name,
                reason=reason,
            )

        return None
