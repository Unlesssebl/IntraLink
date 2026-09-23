"""Scenario: Printer Installation (install_printer)."""

from __future__ import annotations

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    FactActionScenario,
    extract_ticket_text,
)
from shared.domain import ActionProposed, DecisionOutcome, FactRequirement, ScenarioDefinition, ScenarioMatch

PRINTER_INSTALL_SERVICE_IDS = {82, 83}


def _printer_install_match(context: ScenarioContext) -> tuple[bool, float, str]:
    dev_type = context.facts.valid_value("device_type")
    if dev_type in ("audio", "other"):
        return False, 0.0, "non_printer_device"

    text = extract_ticket_text(context)
    if any(tok in text for tok in ("наушник", "колонки", "коллонки", "гарнитур", "микрофон")) and not any(
        tok in text for tok in ("принтер", "мфу", "printer")
    ):
        return False, 0.0, "audio_device"

    task = context.task
    sid = task.get("ServiceId") or task.get("service_id")
    if sid is not None and int(sid) in PRINTER_INSTALL_SERVICE_IDS:
        return True, 0.95, f"service_id:{sid}"

    device_tokens = ("принтер", "мфу", "printer")
    install_tokens = (
        "установ",
        "подключ",
        "добав",
        "настроить новый",
        "настройка нового",
        "переустанов",
    )
    failure_tokens = (
        "не печатает",
        "не сканирует",
        "замят",
        "полос",
        "ошибка печати",
        "очередь зависла",
        "не подключа",
        "не видит",
    )
    has_device = any(token in text for token in device_tokens) or bool(
        context.facts.valid_value("printer_name")
    )
    matched_intents = [token for token in install_tokens if token in text]
    matched_failures = [token for token in failure_tokens if token in text]
    is_reinstall = "переустанов" in text
    matched = has_device and bool(matched_intents) and (not matched_failures or is_reinstall)
    reason = ",".join([*matched_intents, *matched_failures])
    return matched, 0.94 if matched else 0.0, reason


class PrinterInstallScenario(FactActionScenario):
    """Сценарий установки и настройки принтера/МФУ (v2)."""

    def requirements(
        self, context: ScenarioContext
    ) -> tuple[FactRequirement, ...]:
        requirements = [
            FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
            FactRequirement(key="printer_name", clarification_key="clarify_printer_name"),
        ]
        if context.facts.valid_value("printer_connection_type") not in {"usb"}:
            requirements.append(
                FactRequirement(
                    key="printer_address", clarification_key="clarify_printer_address"
                )
            )
        return tuple(requirements)

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        outcome = super().decide(context)
        targets = context.facts.valid_value("printer_targets", [])
        if isinstance(outcome, ActionProposed) and targets:
            outcome.parameters["printer_targets"] = targets
        return outcome


def build_install_printer_scenarios() -> tuple[Scenario, ...]:
    # v1 kept addressable for pinned runs
    v1 = FactActionScenario(
        ScenarioDefinition(
            key="install_printer",
            version=1,
            risk_level=1,
            allowed_actions=["install_printer"],
            clarification_outcome_key="printer_ip_clarify",
            success_outcome_key="resolved_standard",
            required_facts=[
                FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
                FactRequirement(
                    key="printer_name", clarification_key="clarify_printer_name"
                ),
            ],
        ),
        lambda _context: (False, 0.0, "legacy_pinned_only"),
        action="install_printer",
        outcome_key="install_printer_proposed",
        parameter_map={
            "pc_name": "pc_name",
            "printer_name": "printer_name",
            "printer_ip": "printer_address",
        },
    )

    # v2 active definition
    v2 = PrinterInstallScenario(
        ScenarioDefinition(
            key="install_printer",
            version=2,
            risk_level=1,
            allowed_actions=["install_printer"],
            clarification_outcome_key="printer_ip_clarify",
            success_outcome_key="resolved_standard",
            required_facts=[
                FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
                FactRequirement(
                    key="printer_name", clarification_key="clarify_printer_name"
                ),
                FactRequirement(
                    key="printer_address", clarification_key="clarify_printer_address"
                ),
            ],
        ),
        _printer_install_match,
        action="install_printer",
        outcome_key="install_printer_proposed",
        parameter_map={
            "pc_name": "pc_name",
            "printer_name": "printer_name",
            "printer_ip": "printer_address",
        },
    )
    return (v1, v2)
