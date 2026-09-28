"""Case-type profiles used exclusively for intake routing."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CaseTypeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_type: str
    version: str = "1.0.0"
    title: str
    intent_summary: str
    exact_service_ids: frozenset[int] = frozenset()
    service_name_terms: tuple[str, ...] = ()
    lexical_phrases: tuple[str, ...] = ()
    semantic_prototypes: tuple[str, ...] = ()
    assertion_keys: tuple[str, ...] = ()
    red_privacy_zone: bool = False


DEFAULT_CASE_PROFILES = (
    CaseTypeProfile(
        case_type="printer_connection_request",
        title="Подключение принтера",
        intent_summary="Подключить, установить или впервые настроить принтер или МФУ.",
        exact_service_ids=frozenset({183}),
        service_name_terms=(
            "мфу и принтеры → настройка",
            "настройка принтера",
            "установка принтера",
            "подключение принтера",
        ),
        lexical_phrases=("подключить принтер", "установить принтер", "добавить принтер", "настроить мфу"),
        semantic_prototypes=("подключить новый принтер к рабочему компьютеру", "установить сетевое МФУ"),
        assertion_keys=("connect_printer",),
    ),
    CaseTypeProfile(
        case_type="printing_incident",
        title="Инцидент печати",
        intent_summary="Восстановить ранее работавшую печать без преждевременного выбора технической причины.",
        exact_service_ids=frozenset({184}),
        service_name_terms=(
            "мфу и принтеры → ремонт",
            "неисправность печати",
            "проблема с печатью",
            "принтер не печатает",
        ),
        lexical_phrases=(
            "принтер не печатает",
            "не могу распечатать",
            "ошибка печати",
            "зависли документы",
            "очередь печати",
            "печатает не на тот принтер",
        ),
        semantic_prototypes=("ранее принтер работал, теперь документ не печатается", "зависла очередь печати"),
        assertion_keys=("printing_incident", "printer_not_printing", "stuck_print_queue", "wrong_default_printer"),
    ),
    CaseTypeProfile(
        case_type="wireless_access_request",
        title="Доступ к беспроводной сети",
        intent_summary="Предоставить пользователю доступ к корпоративной WLAN.",
        exact_service_ids=frozenset({181}),
        service_name_terms=("wi-fi", "wifi", "wlan", "беспровод"),
        lexical_phrases=("доступ к wi-fi", "доступ к wifi", "корпоративный wi-fi", "wlan-worknet"),
        semantic_prototypes=("добавить пользователя в корпоративную беспроводную сеть",),
        assertion_keys=("wireless_access", "wlan_access"),
        red_privacy_zone=True,
    ),
    CaseTypeProfile(
        case_type="employee_onboarding",
        title="Онбординг сотрудника",
        intent_summary="Создать учётную запись для нового сотрудника.",
        exact_service_ids=frozenset({53}),
        service_name_terms=("создание нового пользователя сети", "создание учетной записи сети", "новый сотрудник"),
        lexical_phrases=("создать учетную запись", "создать пользователя", "новый сотрудник", "выход сотрудника"),
        semantic_prototypes=("создать доменную учетную запись новому сотруднику",),
        assertion_keys=("create_user", "employee_onboarding"),
        red_privacy_zone=True,
    ),
    CaseTypeProfile(
        case_type="access_revocation_request",
        title="Отзыв доступа",
        intent_summary="Заблокировать или отключить доступ сотрудника.",
        exact_service_ids=frozenset({104}),
        service_name_terms=("блокировка пользователя", "увольнение", "блокировка учетной записи", "отзыв доступа"),
        lexical_phrases=(
            "заблокировать учетную запись",
            "увольнение сотрудника",
            "закрыть доступ",
            "отключить пользователя",
        ),
        semantic_prototypes=("деактивировать учетную запись уволенного сотрудника",),
        assertion_keys=("revoke_access", "access_revocation"),
        red_privacy_zone=True,
    ),
    CaseTypeProfile(
        case_type="workstation_unavailable_incident",
        title="Недоступное рабочее место",
        intent_summary="Диагностировать выключенный или недоступный компьютер.",
        exact_service_ids=frozenset(),
        service_name_terms=("не включается", "нет питания", "недоступен пк"),
        lexical_phrases=("компьютер не включается", "черный экран", "нет питания", "пк не отвечает"),
        semantic_prototypes=("рабочая станция не реагирует на кнопку питания",),
        assertion_keys=("workstation_unavailable",),
    ),
    CaseTypeProfile(
        case_type="non_it_request",
        title="Непрофильное обращение",
        intent_summary="Обращение относится к другой службе и требует перенаправления.",
        exact_service_ids=frozenset({999}),
        service_name_terms=("клининг", "хозяйствен", "бухгалтер"),
        lexical_phrases=("уборка", "сломался стул", "заменить лампочку", "акт сверки", "расчетный листок"),
        semantic_prototypes=("требуется хозяйственная работа, не связанная с ИТ",),
        assertion_keys=("non_it_request",),
    ),
    CaseTypeProfile(
        case_type="software_installation_request",
        title="Установка программного обеспечения",
        intent_summary="Установить или настроить программное обеспечение на рабочем месте.",
        exact_service_ids=frozenset({59}),
        service_name_terms=("установка и настройка программ", "установка, настройка (по)"),
        lexical_phrases=("установить программу", "установить по", "настроить программу", "установка программы"),
        semantic_prototypes=("установить или настроить программное обеспечение на компьютере",),
        assertion_keys=("install_software",),
    ),
    CaseTypeProfile(
        case_type="knowledge_request",
        title="Консультация",
        intent_summary="Пользователю нужна справочная информация без инфраструктурной мутации.",
        lexical_phrases=("как настроить", "подскажите как", "нужна инструкция", "где найти инструкцию"),
        semantic_prototypes=("общий вопрос по работе системы, нужна консультация",),
        assertion_keys=("knowledge_request",),
    ),
)


class CaseProfileRegistry:
    def __init__(self, profiles: tuple[CaseTypeProfile, ...] = DEFAULT_CASE_PROFILES) -> None:
        self._profiles = {profile.case_type: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("Case type profile keys must be unique")

    def get(self, case_type: str) -> CaseTypeProfile | None:
        return self._profiles.get(case_type)

    def list_all(self) -> list[CaseTypeProfile]:
        return sorted(self._profiles.values(), key=lambda item: item.case_type)
