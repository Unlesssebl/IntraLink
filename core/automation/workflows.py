"""Versioned first-line workflow catalog for ADR 0006."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from core.automation.contracts import Disposition


class FactNormalization(str, Enum):
    none = "none"
    capitalize = "capitalize"
    upper = "upper"
    lower = "lower"
    trim = "trim"


class WorkflowFactSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    description: str = ""
    synonyms: tuple[str, ...] = ()
    pattern: str | None = None
    required: bool = True
    normalization: FactNormalization = FactNormalization.trim

    def normalize(self, raw_value: str) -> str:
        clean = " ".join(raw_value.split())
        if not clean:
            return ""
        if self.normalization == FactNormalization.capitalize:
            return clean.capitalize()
        if self.normalization == FactNormalization.upper:
            return clean.upper()
        if self.normalization == FactNormalization.lower:
            return clean.lower()
        return clean


class WorkflowPresentationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    icon: str = "route"  # "user", "printer", "network", "folder", "route", "wrench"
    category_badge: str = "Автоматизация"
    subject_label: str = "Параметры"
    subject_template: str = ""
    subject_caption_template: str = ""


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    version: str
    name: str
    case_types: tuple[str, ...]
    facts: tuple[WorkflowFactSpec, ...] = ()
    required_facts: tuple[str, ...] = ()
    diagnostic_capabilities: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    default_disposition: Disposition
    max_clarification_rounds: int = 2
    presentation: WorkflowPresentationSpec = WorkflowPresentationSpec()
    auto_execution_eligible: bool = False

    @model_validator(mode="before")
    @classmethod
    def _sync_required_facts(cls, data: Any) -> Any:
        if isinstance(data, dict):
            facts = data.get("facts", ())
            if "required_facts" not in data and facts:
                req_keys: list[str] = []
                for f in facts:
                    if isinstance(f, WorkflowFactSpec):
                        if f.required:
                            req_keys.append(f.key)
                    elif isinstance(f, dict):
                        if f.get("required", True):
                            req_keys.append(f["key"])
                data["required_facts"] = tuple(req_keys)
        return data

    def fact_spec(self, key: str) -> WorkflowFactSpec | None:
        for f in self.facts:
            if f.key == key:
                return f
        return None


class WorkflowRegistry:
    def __init__(self, definitions: tuple[WorkflowDefinition, ...] = ()) -> None:
        self._by_key: dict[str, WorkflowDefinition] = {}
        self._by_case_type: dict[str, WorkflowDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: WorkflowDefinition) -> None:
        if definition.key in self._by_key:
            raise ValueError(f"Duplicate workflow key: {definition.key}")
        for case_type in definition.case_types:
            if case_type in self._by_case_type:
                raise ValueError(f"Case type '{case_type}' is already owned by a workflow")
        self._by_key[definition.key] = definition
        for case_type in definition.case_types:
            self._by_case_type[case_type] = definition

    def get(self, key: str) -> WorkflowDefinition | None:
        return self._by_key.get(key)

    def for_case_type(self, case_type: str) -> WorkflowDefinition | None:
        return self._by_case_type.get(case_type)

    def list_all(self) -> list[WorkflowDefinition]:
        return sorted(self._by_key.values(), key=lambda item: item.key)


DEFAULT_WORKFLOWS = (
    WorkflowDefinition(
        key="printer_connection_workflow",
        version="1.0.0",
        name="Подключение и настройка принтера",
        case_types=("printer_connection_request",),
        facts=(
            WorkflowFactSpec(
                key="pc_name",
                label="Имя компьютера",
                synonyms=("компьютер", "пк", "хост", "arm", "комп", "рабочая станция"),
                normalization=FactNormalization.upper,
            ),
            WorkflowFactSpec(
                key="connection_type",
                label="Тип подключения",
                synonyms=("тип подключения", "подключение", "сетевой", "usb"),
            ),
            WorkflowFactSpec(
                key="printer_name_or_ip",
                label="Принтер / IP",
                synonyms=("принтер", "мфу", "печать", "ip принтера", "сетевой принтер"),
            ),
            WorkflowFactSpec(
                key="room",
                label="Кабинет",
                synonyms=("кабинет", "комната", "офис", "каб"),
            ),
        ),
        required_facts=("pc_name", "connection_type"),
        diagnostic_capabilities=("probe_host", "probe_printer"),
        allowed_capabilities=("install_printer",),
        default_disposition=Disposition.execute,
        presentation=WorkflowPresentationSpec(
            icon="printer",
            category_badge="Оборудование",
            subject_label="Рабочее место",
            subject_template="{pc_name}",
            subject_caption_template="{printer_address}",
        ),
    ),
    WorkflowDefinition(
        key="printing_incident_workflow",
        version="1.0.0",
        name="Диагностика инцидента печати",
        case_types=("printing_incident",),
        facts=(
            WorkflowFactSpec(
                key="pc_name",
                label="Имя компьютера",
                synonyms=("компьютер", "пк", "хост", "arm", "комп", "рабочая станция"),
                normalization=FactNormalization.upper,
            ),
        ),
        required_facts=("pc_name",),
        diagnostic_capabilities=("probe_host",),
        allowed_capabilities=("reset_print_spooler", "set_default_printer"),
        default_disposition=Disposition.manual,
        presentation=WorkflowPresentationSpec(
            icon="printer",
            category_badge="Оборудование",
            subject_label="Рабочее место",
            subject_template="{pc_name}",
            subject_caption_template="Инцидент печати",
        ),
    ),
    WorkflowDefinition(
        key="wireless_access_workflow",
        version="1.0.0",
        name="Предоставление доступа к WLAN",
        case_types=("wireless_access_request",),
        facts=(
            WorkflowFactSpec(
                key="target_user",
                label="Целевой пользователь",
                synonyms=("пользователь", "сотрудник", "логин", "учетная запись"),
            ),
            WorkflowFactSpec(
                key="mac_address",
                label="MAC-адрес",
                synonyms=("мак", "mac", "мак-адрес", "mac-адрес"),
                pattern=r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$",
                normalization=FactNormalization.upper,
            ),
            WorkflowFactSpec(
                key="device_type",
                label="Тип устройства",
                synonyms=("устройство", "телефон", "смартфон", "ноутбук", "планшет"),
            ),
        ),
        required_facts=("target_user",),
        allowed_capabilities=("add_wlan_group_member",),
        default_disposition=Disposition.execute,
        auto_execution_eligible=True,
        presentation=WorkflowPresentationSpec(
            icon="network",
            category_badge="Сетевой доступ",
            subject_label="Целевой пользователь",
            subject_template="{target_user}",
            subject_caption_template="WLAN доступ",
        ),
    ),
    WorkflowDefinition(
        key="employee_onboarding_workflow",
        version="1.0.0",
        name="Онбординг сотрудника",
        case_types=("employee_onboarding",),
        facts=(
            WorkflowFactSpec(
                key="last_name",
                label="Фамилия",
                synonyms=("фамилия", "last name", "фамилия сотрудника"),
                normalization=FactNormalization.capitalize,
            ),
            WorkflowFactSpec(
                key="first_name",
                label="Имя",
                synonyms=("имя", "first name", "имя сотрудника"),
                normalization=FactNormalization.capitalize,
            ),
            WorkflowFactSpec(
                key="middle_name",
                label="Отчество",
                synonyms=("отчество", "middle name"),
                normalization=FactNormalization.capitalize,
            ),
            WorkflowFactSpec(
                key="department",
                label="Подразделение",
                synonyms=("отдел", "подразделение", "департамент", "служба", "управление"),
            ),
            WorkflowFactSpec(
                key="title",
                label="Должность",
                synonyms=("должность", "позиция", "профессия"),
            ),
            WorkflowFactSpec(
                key="company",
                label="Организация",
                synonyms=("организация", "компания", "фирма", "предприятие", "юрлицо"),
            ),
            WorkflowFactSpec(
                key="phone",
                label="Телефон",
                synonyms=("телефон", "тел", "внутренний телефон", "моб"),
            ),
        ),
        required_facts=("first_name", "last_name", "department", "title"),
        allowed_capabilities=("create_ad_user",),
        default_disposition=Disposition.execute,
        presentation=WorkflowPresentationSpec(
            icon="user",
            category_badge="Учётные записи",
            subject_label="Новый сотрудник",
            subject_template="{last_name} {first_name} {middle_name}",
            subject_caption_template="{department} · {title}",
        ),
    ),
    WorkflowDefinition(
        key="access_revocation_workflow",
        version="1.0.0",
        name="Отзыв доступа сотрудника",
        case_types=("access_revocation_request",),
        facts=(
            WorkflowFactSpec(
                key="target_user",
                label="Целевой пользователь",
                synonyms=("пользователь", "сотрудник", "логин", "учетная запись"),
            ),
        ),
        required_facts=("target_user",),
        allowed_capabilities=("disable_ad_user",),
        default_disposition=Disposition.execute,
        presentation=WorkflowPresentationSpec(
            icon="user",
            category_badge="Учётные записи",
            subject_label="Целевой пользователь",
            subject_template="{target_user}",
            subject_caption_template="Отзыв доступа",
        ),
    ),
    WorkflowDefinition(
        key="workstation_unavailable_workflow",
        version="1.0.0",
        name="Диагностика недоступного рабочего места",
        case_types=("workstation_unavailable_incident",),
        facts=(
            WorkflowFactSpec(
                key="pc_name",
                label="Имя компьютера",
                synonyms=("компьютер", "пк", "хост", "arm", "комп"),
                normalization=FactNormalization.upper,
            ),
        ),
        required_facts=("pc_name",),
        diagnostic_capabilities=("probe_host",),
        default_disposition=Disposition.manual,
        presentation=WorkflowPresentationSpec(
            icon="wrench",
            category_badge="Диагностика",
            subject_label="Рабочее место",
            subject_template="{pc_name}",
            subject_caption_template="Сетевая недоступность",
        ),
    ),
    WorkflowDefinition(
        key="workstation_hardware_diagnostic_workflow",
        version="1.0.0",
        name="Диагностика оборудования рабочего места",
        case_types=("workstation_hardware_diagnostic",),
        facts=(
            WorkflowFactSpec(
                key="pc_name",
                label="Имя компьютера",
                synonyms=("компьютер", "пк", "хост", "arm", "комп"),
                required=False,
                normalization=FactNormalization.upper,
            ),
        ),
        diagnostic_capabilities=("probe_host",),
        default_disposition=Disposition.manual,
        presentation=WorkflowPresentationSpec(
            icon="wrench",
            category_badge="Диагностика",
            subject_label="Рабочее место",
            subject_template="{pc_name}",
            subject_caption_template="Аппаратная диагностика",
        ),
    ),
    WorkflowDefinition(
        key="knowledge_consultation_workflow",
        version="1.0.0",
        name="Консультация пользователя",
        case_types=("knowledge_request",),
        default_disposition=Disposition.consult,
        presentation=WorkflowPresentationSpec(
            icon="route",
            category_badge="База знаний",
            subject_label="Тема консультации",
            subject_template="Консультация",
            subject_caption_template="База знаний Helpdesk",
        ),
    ),
    WorkflowDefinition(
        key="manual_review_workflow",
        version="1.0.0",
        name="Ручная обработка обращения",
        case_types=("non_it_request",),
        default_disposition=Disposition.manual,
        presentation=WorkflowPresentationSpec(
            icon="route",
            category_badge="Обработка",
            subject_label="Параметры обращения",
            subject_template="Ручной разбор",
            subject_caption_template="Требуется ручной разбор",
        ),
    ),
)


def get_default_workflow_registry() -> WorkflowRegistry:
    return WorkflowRegistry(DEFAULT_WORKFLOWS)
