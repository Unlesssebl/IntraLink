"""Versioned first-line workflow catalog for ADR 0006."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from core.automation.contracts import Disposition


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    version: str
    name: str
    case_types: tuple[str, ...]
    required_facts: tuple[str, ...] = ()
    diagnostic_capabilities: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    default_disposition: Disposition
    max_clarification_rounds: int = 2


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
        required_facts=("pc_name", "connection_type"),
        diagnostic_capabilities=("probe_host", "probe_printer"),
        allowed_capabilities=("install_printer",),
        default_disposition=Disposition.execute,
    ),
    WorkflowDefinition(
        key="printing_incident_workflow",
        version="1.0.0",
        name="Диагностика инцидента печати",
        case_types=("printing_incident",),
        required_facts=("pc_name",),
        diagnostic_capabilities=("probe_host",),
        allowed_capabilities=("reset_print_spooler", "set_default_printer"),
        default_disposition=Disposition.manual,
    ),
    WorkflowDefinition(
        key="wireless_access_workflow",
        version="1.0.0",
        name="Предоставление доступа к WLAN",
        case_types=("wireless_access_request",),
        required_facts=("target_user",),
        allowed_capabilities=("add_wlan_group_member",),
        default_disposition=Disposition.execute,
    ),
    WorkflowDefinition(
        key="employee_onboarding_workflow",
        version="1.0.0",
        name="Онбординг сотрудника",
        case_types=("employee_onboarding",),
        required_facts=("first_name", "last_name", "department", "title"),
        allowed_capabilities=("create_ad_user",),
        default_disposition=Disposition.execute,
    ),
    WorkflowDefinition(
        key="access_revocation_workflow",
        version="1.0.0",
        name="Отзыв доступа сотрудника",
        case_types=("access_revocation_request",),
        required_facts=("target_user",),
        allowed_capabilities=("disable_ad_user",),
        default_disposition=Disposition.execute,
    ),
    WorkflowDefinition(
        key="workstation_unavailable_workflow",
        version="1.0.0",
        name="Диагностика недоступного рабочего места",
        case_types=("workstation_unavailable_incident",),
        required_facts=("pc_name",),
        diagnostic_capabilities=("probe_host",),
        default_disposition=Disposition.manual,
    ),
    WorkflowDefinition(
        key="knowledge_consultation_workflow",
        version="1.0.0",
        name="Консультация пользователя",
        case_types=("knowledge_request",),
        default_disposition=Disposition.consult,
    ),
    WorkflowDefinition(
        key="non_it_redirect_workflow",
        version="1.0.0",
        name="Передача непрофильного обращения",
        case_types=("non_it_request",),
        default_disposition=Disposition.redirect,
    ),
)


def get_default_workflow_registry() -> WorkflowRegistry:
    return WorkflowRegistry(DEFAULT_WORKFLOWS)
