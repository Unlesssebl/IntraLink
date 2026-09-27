"""Canonical scenario routing profiles for Evidence-Based Routing Cascade.

Defines immutable metadata-only ScenarioRoutingProfile declarations representing
deterministic routing parameters, catalog mapping, and entity requirements
without any execution logic or side effects.
"""

from typing import Tuple

from pydantic import BaseModel, ConfigDict, Field


class ScenarioRoutingProfile(BaseModel):
    """Immutable routing profile defining signals and requirements for a scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_key: str
    scenario_version: str
    intent_summary: str
    exact_service_ids: frozenset[int] = Field(default_factory=frozenset)
    service_name_terms: Tuple[str, ...] = Field(default_factory=tuple)
    lexical_phrases: Tuple[str, ...] = Field(default_factory=tuple)
    semantic_prototypes: Tuple[str, ...] = Field(default_factory=tuple)
    required_facts: Tuple[str, ...] = Field(default_factory=tuple)
    consultation_only: bool = False


# Canonical profile definitions strictly matching existing registered scenarios
INSTALL_PRINTER_PROFILE = ScenarioRoutingProfile(
    scenario_key="install_printer",
    scenario_version="1.0.0",
    intent_summary="Установка, подключение или первичная настройка локального либо сетевого принтера/МФУ на рабочей станции пользователя.",
    exact_service_ids=frozenset({19, 62, 82, 83, 183}),
    service_name_terms=("принтер", "печать", "оргтехник", "мфу", "сканер"),
    lexical_phrases=(
        "принтер",
        "мфу",
        "печать",
        "установка принтера",
        "подключить принтер",
        "настроить принтер",
        "добавить принтер",
        "установить принтер",
        "подключение принтера",
        "настройка принтера",
        "установка мфу",
        "подключить мфу",
        "настроить мфу",
    ),
    semantic_prototypes=(
        "не могу подключить принтер к компьютеру",
        "установить сетевой принтер на рабочей станции",
        "принтер не печатает, ошибка драйвера Canon Kyocera",
        "нужно настроить МФУ в офисе, не добавляется в Windows",
        "принтер недоступен, не отображается в сети",
    ),
    required_facts=("pc_name", "printer_address"),
    consultation_only=False,
)

PRINTER_SPOOLER_RESTART_PROFILE = ScenarioRoutingProfile(
    scenario_key="printer_spooler_restart",
    scenario_version="1.0.0",
    intent_summary="Устранение зависшей очереди печати, перезапуск локальной службы диспетчера печати (Spooler) и очистка застрявших заданий.",
    exact_service_ids=frozenset({12, 19, 62, 82, 83, 183}),
    service_name_terms=("принтер", "печать", "оргтехник", "мфу", "сканер"),
    lexical_phrases=(
        "не печатает принтер",
        "принтер не печатает",
        "не могу распечатать",
        "ошибка печати",
        "зависла печать",
        "завис документ",
        "зависли документы",
        "очистить очередь",
        "сбросить очередь",
        "сброс очереди",
        "очередь печати",
        "документы висят в очереди",
        "печать не идет",
        "печать не уходит",
        "перезапустить spooler",
        "перезапустить спулер",
        "перезапуск службы печати",
        "диспетчер печати остановлен",
        "служба печати остановлена",
        "печать заблокирована",
        "документ не печатается",
    ),
    semantic_prototypes=(
        "зависла печать",
        "очистить очередь печати",
        "печать не идет, документы висят в очереди",
        "перезапустить диспетчер печати spooler",
        "застрял документ в очереди принтера",
        "ошибка очереди печати на компьютере",
        "сброс очереди печати",
    ),
    required_facts=("pc_name",),
    consultation_only=False,
)

DEFAULT_PRINTER_FIX_PROFILE = ScenarioRoutingProfile(
    scenario_key="default_printer_fix",
    scenario_version="1.0.0",
    intent_summary="Назначение и восстановление корректного основного принтера по умолчанию в профиле пользователя Windows.",
    exact_service_ids=frozenset({12, 19, 62, 82, 83, 183}),
    service_name_terms=("принтер", "печать", "оргтехник", "мфу", "сканер"),
    lexical_phrases=(
        "по умолчанию",
        "дефолтн",
        "основной принтер",
        "слетел принтер",
        "не тот принтер",
        "выбрать принтер основным",
        "назначить принтером по умолчанию",
        "сделать принтер дефолтным",
        "поставить по умолчанию",
    ),
    semantic_prototypes=(
        "поставить принтер по умолчанию",
        "назначить основной принтер",
        "слетел принтер по умолчанию",
        "печать отправляется не на тот принтер",
        "сделать принтер дефолтным",
        "выбрать принтер основным",
    ),
    required_facts=("pc_name", "printer_address"),
    consultation_only=False,
)

GRANT_WLAN_PROFILE = ScenarioRoutingProfile(
    scenario_key="grant_wlan",
    scenario_version="1.0.0",
    intent_summary="Предоставление пользователю доступа к корпоративной беспроводной сети (WLAN/Wi-Fi) и добавление в группу доступа.",
    exact_service_ids=frozenset({63}),
    service_name_terms=("wi-fi", "wifi", "вайфай", "беспроводн", "wlan"),
    lexical_phrases=(
        "wlan-worknet",
        "доступ к wi-fi",
        "доступ к wifi",
        "доступ к вайфай",
        "подключение к wi-fi",
        "подключение к wifi",
        "подключить к wi-fi",
        "подключить ноутбук к wi-fi",
        "корпоративный wi-fi",
        "пароль от wi-fi",
        "вай-фай",
        "вайфай",
        "wi-fi",
        "wifi",
        "wlan",
    ),
    semantic_prototypes=(
        "прошу предоставить доступ к корпоративному Wi-Fi WLAN-WORKNET",
        "нужно подключить ноутбук к беспроводной сети компании",
        "нет доступа к wifi в офисе, требуется добавить в группу",
        "хочу подключиться к вайфай, добавьте мою учетку",
        "беспроводная сеть не доступна для моего устройства",
    ),
    required_facts=("target_user",),
    consultation_only=False,
)

ACCOUNT_CREATE_PROFILE = ScenarioRoutingProfile(
    scenario_key="account_create",
    scenario_version="1.0.0",
    intent_summary="Создание новой пользовательской учётной записи сотрудника в Active Directory / информационных системах (онбординг).",
    exact_service_ids=frozenset({55, 232}),
    service_name_terms=(
        "создать пользователя",
        "создание учетной записи",
        "новый сотрудник",
        "онбординг",
        "пользовател directum",
    ),
    lexical_phrases=(
        "создать учет",
        "создать учёт",
        "создание учет",
        "создание учёт",
        "создать пользователя",
        "создание пользователя",
        "завести пользователя",
        "завести сотрудника",
        "новый пользователь",
        "новый сотрудник",
        "создание уз",
        "создать уз",
        "новая учетная запись",
        "заявка на создание пользователя",
        "заявка на создание учетной записи",
        "заявка на пользователя директум",
        "заявка на пользователя directum",
        "выход сотрудника",
    ),
    semantic_prototypes=(
        "создать учетку нового сотрудника",
        "выход сотрудника",
        "заявка на пользователя директум",
        "создание учетной записи в Active Directory",
        "завести пользователя в домене",
        "создать учетную запись новому сотруднику",
        "оформить доступ новому сотруднику",
    ),
    required_facts=("first_name", "last_name", "department", "title"),
    consultation_only=False,
)

ACCOUNT_LOCK_PROFILE = ScenarioRoutingProfile(
    scenario_key="account_lock",
    scenario_version="1.0.0",
    intent_summary="Блокировка и деактивация учётной записи пользователя в связи с увольнением или отзывом доступа (оффбординг).",
    exact_service_ids=frozenset({8}),
    service_name_terms=(
        "увольнение",
        "блокировка учетной записи",
        "заблокировать",
        "оффбординг",
    ),
    lexical_phrases=(
        "увольнен",
        "уволен",
        "уволить",
        "заблокировать учет",
        "заблокировать учёт",
        "заблокировать пользовател",
        "блокировка учет",
        "блокировка учёт",
        "блокировка пользовател",
        "закрыть доступ",
        "отозвать доступ",
        "отключить учет",
        "отключить учёт",
        "отключить пользовател",
        "заблокировать уз",
        "блокировка уз",
        "заблокировать логин",
    ),
    semantic_prototypes=(
        "увольнение сотрудника",
        "заблокировать учетную запись",
        "закрыть доступы",
        "отключить учетную запись в AD",
        "блокировка пользователя в связи с увольнением",
        "заблокировать логин сотрудника",
        "деактивировать учетную запись уволенного",
    ),
    required_facts=("target_user",),
    consultation_only=False,
)

SERVICE_REDIRECT_PROFILE = ScenarioRoutingProfile(
    scenario_key="service_redirect",
    scenario_version="1.0.0",
    intent_summary="Перенаправление непрофильной заявки в сторонние службы (1C, Directum, бухгалтерия, АХО, клининг) вне зоны ответственности ServiceDesk.",
    exact_service_ids=frozenset({999}),
    service_name_terms=("1с", "клининг", "бухгалтер", "хоз"),
    lexical_phrases=(
        "directum",
        "директум",
        "согласование договора",
        "служебная записка в directum",
        "1с",
        "1c",
        "зуп",
        "бухгалтери",
        "проводк",
        "акт сверки",
        "клининг",
        "уборк",
        "помыть",
        "лампочк",
        "кондиционер",
        "стул",
        "стол",
        "замок",
        "дверь",
        "жалюзи",
        "расчетный листок",
        "отпуск",
        "справка 2-ндфл",
        "трудовая книжка",
    ),
    semantic_prototypes=(
        "заявка на установку программы 1С для бухгалтерии",
        "вопрос по системе Directum, договора и тендеры",
        "заказать канцелярию, прошу выдать бумагу и ручки",
        "нужен пропуск для посетителя в офис",
        "клининг не приходил, уборка помещения",
    ),
    required_facts=(),
    consultation_only=False,
)

OFFLINE_HOST_PROFILE = ScenarioRoutingProfile(
    scenario_key="offline_host",
    scenario_version="1.0.0",
    intent_summary="Первичная диагностика и реагирование на физически выключенный, зависший или недоступный по сети компьютер пользователя.",
    exact_service_ids=frozenset({71, 112}),
    service_name_terms=("не включается", "нет питания", "не доступен пк", "оффлайн"),
    lexical_phrases=(
        "не включается",
        "не работает компьютер",
        "не включается пк",
        "черный экран",
        "нет питания",
        "компьютер выключен",
        "пк не отвечает",
        "нет сети на компьютере",
        "недоступен компьютер",
        "не загружается windows",
    ),
    semantic_prototypes=(
        "компьютер не включается, чёрный экран, нет питания",
        "рабочая станция не реагирует на нажатие кнопки питания",
        "ПК не загружается, гудит кулер но монитор пустой",
        "не могу запустить компьютер в кабинете, запах гари",
        "рабочее место полностью недоступно, компьютер мертвый",
    ),
    required_facts=("pc_name",),
    consultation_only=False,
)

RAG_CONSULTATION_PROFILE = ScenarioRoutingProfile(
    scenario_key="rag_consultation",
    scenario_version="1.0.0",
    intent_summary="Предоставление справочной консультации и инструкций из базы знаний по нетиповым вопросам и прикладным ошибкам.",
    exact_service_ids=frozenset(),
    service_name_terms=(),
    lexical_phrases=(),
    semantic_prototypes=(
        "не знаю куда обратиться, общий вопрос по работе системы",
        "не работает приложение, непонятная ошибка при запуске",
        "возникла нестандартная ситуация, нужна консультация",
        "медленно работает интернет, теряются пакеты",
        "вопрос по настройке рабочего места, не знаю к кому идти",
    ),
    required_facts=(),
    consultation_only=True,
)

DEFAULT_PROFILES: Tuple[ScenarioRoutingProfile, ...] = (
    INSTALL_PRINTER_PROFILE,
    PRINTER_SPOOLER_RESTART_PROFILE,
    DEFAULT_PRINTER_FIX_PROFILE,
    GRANT_WLAN_PROFILE,
    ACCOUNT_CREATE_PROFILE,
    ACCOUNT_LOCK_PROFILE,
    SERVICE_REDIRECT_PROFILE,
    OFFLINE_HOST_PROFILE,
    RAG_CONSULTATION_PROFILE,
)
