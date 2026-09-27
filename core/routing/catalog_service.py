"""Server-side scenario catalog builder for Operator UX and Human-in-the-loop Correction."""

from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from core.autopilot.dto import ScenarioCatalogItemDTO, ScenarioFieldSchemaDTO
from core.autopilot.policy_service import AutopilotPolicyService, get_policy_service
from core.routing.profile_registry import (
    RoutingProfileRegistry,
    get_default_profile_registry,
)
from core.scenarios.registry import (
    ScenarioRegistry,
    get_default_scenario_registry,
)

# Canonical operator-editable field schemas per scenario (passwords and secrets strictly excluded)
_OPERATOR_FIELD_SCHEMAS: Dict[str, List[ScenarioFieldSchemaDTO]] = {
    "install_printer": [
        ScenarioFieldSchemaDTO(
            field_key="pc_name",
            label="Имя компьютера (Host)",
            field_type="string",
            required=True,
            hint="например: W10-042, PC-IT-01",
        ),
        ScenarioFieldSchemaDTO(
            field_key="printer_address",
            label="IP-адрес / Очередь принтера",
            field_type="string",
            required=True,
            hint="например: 10.20.4.15, PRN-305",
        ),
        ScenarioFieldSchemaDTO(
            field_key="printer_model",
            label="Модель устройства",
            field_type="string",
            required=False,
            hint="например: HP LaserJet Pro M404, Kyocera M2040dn",
        ),
    ],
    "printer_spooler_restart": [
        ScenarioFieldSchemaDTO(
            field_key="pc_name",
            label="Имя компьютера (Host)",
            field_type="string",
            required=True,
            hint="например: W10-042",
        ),
    ],
    "default_printer_fix": [
        ScenarioFieldSchemaDTO(
            field_key="pc_name",
            label="Имя компьютера (Host)",
            field_type="string",
            required=True,
            hint="например: W10-042",
        ),
        ScenarioFieldSchemaDTO(
            field_key="printer_name",
            label="Имя принтера по умолчанию",
            field_type="string",
            required=False,
            hint="например: HP LaserJet P2035",
        ),
    ],
    "grant_wlan": [
        ScenarioFieldSchemaDTO(
            field_key="target_user",
            label="Логин заявителя (Active Directory)",
            field_type="string",
            required=True,
            hint="например: ivanov.i, petrov.p",
        ),
    ],
    "account_lock": [
        ScenarioFieldSchemaDTO(
            field_key="target_user",
            label="Логин сотрудника для блокировки",
            field_type="string",
            required=True,
            hint="например: sidorov.s",
        ),
        ScenarioFieldSchemaDTO(
            field_key="reason",
            label="Основание / Приказ",
            field_type="string",
            required=False,
            hint="например: Приказ об увольнении №124 от 26.09.2026",
        ),
    ],
    "account_create": [
        ScenarioFieldSchemaDTO(
            field_key="target_user",
            label="ФИО / Логин нового сотрудника",
            field_type="string",
            required=True,
            hint="например: Смирнов Алексей",
        ),
        ScenarioFieldSchemaDTO(
            field_key="department",
            label="Подразделение / Должность",
            field_type="string",
            required=False,
            hint="например: Отдел логистики",
        ),
    ],
    "ad_password_reset": [],  # Disabled scenario: password strictly excluded
    "offline_host": [
        ScenarioFieldSchemaDTO(
            field_key="pc_name",
            label="Имя компьютера",
            field_type="string",
            required=True,
            hint="например: W10-042",
        ),
    ],
    "service_redirect": [
        ScenarioFieldSchemaDTO(
            field_key="target_service_id",
            label="ID целевого сервиса",
            field_type="integer",
            required=False,
            hint="например: 40 (Directum)",
        ),
        ScenarioFieldSchemaDTO(
            field_key="reason",
            label="Причина перенаправления",
            field_type="string",
            required=False,
            hint="например: Заявка создана не в том разделе каталога",
        ),
    ],
    "rag_consultation": [
        ScenarioFieldSchemaDTO(
            field_key="query",
            label="Тема консультации",
            field_type="string",
            required=False,
            hint="например: Доступ к сетевому диску",
        ),
    ],
}


class ScenarioCatalogService:
    """Service dynamically synthesizing actionable scenario catalog for operator correction."""

    def __init__(
        self,
        policy_service: Optional[AutopilotPolicyService] = None,
        scenario_registry: Optional[ScenarioRegistry] = None,
        profile_registry: Optional[RoutingProfileRegistry] = None,
    ) -> None:
        self.policy_service = policy_service or get_policy_service()
        self.scenario_registry = scenario_registry or get_default_scenario_registry()
        self.profile_registry = profile_registry or get_default_profile_registry()

    async def get_catalog(
        self,
        session: Optional[AsyncSession] = None,
    ) -> List[ScenarioCatalogItemDTO]:
        """Build complete scenario catalog with policy modes, fact requirements and editable fields."""
        policies = await self.policy_service.list_policies(session=session)
        policy_by_key = {p.scenario_key: p for p in policies}

        catalog: List[ScenarioCatalogItemDTO] = []

        for profile in self.profile_registry.list_all():
            key = profile.scenario_key
            scenario_exec = self.scenario_registry.get(key)
            pol = policy_by_key.get(key)

            mode = pol.mode if pol else "ASSISTED"
            is_circuit_broken = pol.is_circuit_broken if pol else False

            name = getattr(scenario_exec, "name", key)
            desc = getattr(scenario_exec, "description", profile.intent_summary)

            is_enabled = mode != "DISABLED"
            disabled_reason = None
            if mode == "DISABLED":
                disabled_reason = "Сценарий отключен политикой безопасности автопилота."
            elif is_circuit_broken:
                disabled_reason = "Предохранитель (Circuit Breaker) активен. Доступно только ручное исполнение."

            # Supported executor
            if key in ("install_printer", "printer_spooler_restart", "default_printer_fix"):
                supported_executor = "windows_exec"
            elif key in ("service_redirect", "rag_consultation"):
                supported_executor = "api"
            else:
                supported_executor = "intralink_worker"

            editable_fields = _OPERATOR_FIELD_SCHEMAS.get(key, [])

            item = ScenarioCatalogItemDTO(
                scenario_key=key,
                name=name,
                description=desc,
                policy_mode=mode,
                is_enabled=is_enabled,
                required_facts=list(profile.required_facts),
                editable_fields=editable_fields,
                supported_executor=supported_executor,
                disabled_reason=disabled_reason,
            )
            catalog.append(item)

        return catalog
