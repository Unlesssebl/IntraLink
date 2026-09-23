"""Scenarios: Printer Failure Handling (hardware service, scan failure, print failure)."""

from __future__ import annotations

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    Matcher,
    RuleBackedScenario,
    default_standard_in_work_outcome,
    extract_ticket_text,
)
from shared.domain import FactRequirement, ScenarioDefinition


def _printer_failure_match(kind: str) -> Matcher:
    def matcher(context: ScenarioContext) -> tuple[bool, float, str]:
        text = extract_ticket_text(context)
        has_device = any(token in text for token in ("принтер", "мфу", "плоттер", "сканер"))
        business_document = any(
            token in text
            for token in ("ттн", "доверенност", "подпис", "оплат", "в программе тис")
        )
        if not has_device or business_document:
            return False, 0.0, ""
        patterns = {
            "printer_hardware_service": ("вызовите сервис", "сбой аппарата", "c7990", "с7990", "замят"),
            "printer_scan_failure": ("не сканирует", "скан не", "ошибка скан", "сканер"),
            "printer_print_failure": ("не печатает", "нет принтера", "очередь", "без остановки", "не принимает задания"),
        }
        found = [token for token in patterns[kind] if token in text]
        return bool(found), 0.96 if found else 0.0, ",".join(found)

    return matcher


class PrinterHardwareServiceScenario(RuleBackedScenario):
    """Сценарий сервисного обслуживания аппаратной части принтера/МФУ."""
    def requirements(self, context: ScenarioContext) -> tuple[FactRequirement, ...]:
        return (
            FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
        )


class PrinterScanFailureScenario(RuleBackedScenario):
    """Сценарий сбоя сканирования."""
    def requirements(self, context: ScenarioContext) -> tuple[FactRequirement, ...]:
        conn_type = context.facts.valid_value("connection_type")
        if conn_type == "network":
            return (
                FactRequirement(key="printer_address", clarification_key="clarify_printer_address"),
            )
        if conn_type == "usb":
            return (
                FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
            )
        return (
            FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
            FactRequirement(key="connection_type", clarification_key="clarify_scan_connection"),
        )


class PrinterPrintFailureScenario(RuleBackedScenario):
    """Сценарий сбоя печати / зависшей очереди."""
    def requirements(self, context: ScenarioContext) -> tuple[FactRequirement, ...]:
        return (
            FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
        )


def build_printer_failure_scenarios() -> tuple[Scenario, ...]:
    return (
        # Legacy v1 definitions kept addressable for pinned runs
        RuleBackedScenario(
            ScenarioDefinition(
                key="printer_hardware_service",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            lambda _context: (False, 0.0, "legacy_pinned_only"),
            default_standard_in_work_outcome,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="printer_scan_failure",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            lambda _context: (False, 0.0, "legacy_pinned_only"),
            default_standard_in_work_outcome,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="printer_print_failure",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            lambda _context: (False, 0.0, "legacy_pinned_only"),
            default_standard_in_work_outcome,
        ),
        # Modern v2 active definitions
        PrinterHardwareServiceScenario(
            ScenarioDefinition(
                key="printer_hardware_service",
                version=2,
                risk_level=0,
                allowed_actions=["apply_triage"],
                clarification_outcome_key="defect_type_clarify",
            ),
            _printer_failure_match("printer_hardware_service"),
            default_standard_in_work_outcome,
        ),
        PrinterScanFailureScenario(
            ScenarioDefinition(
                key="printer_scan_failure",
                version=2,
                risk_level=0,
                allowed_actions=["apply_triage"],
                clarification_outcome_key="scan_connection_clarify",
            ),
            _printer_failure_match("printer_scan_failure"),
            default_standard_in_work_outcome,
        ),
        PrinterPrintFailureScenario(
            ScenarioDefinition(
                key="printer_print_failure",
                version=2,
                risk_level=0,
                allowed_actions=["apply_triage"],
                clarification_outcome_key="printer_queue_clarify",
            ),
            _printer_failure_match("printer_print_failure"),
            default_standard_in_work_outcome,
        ),
    )
