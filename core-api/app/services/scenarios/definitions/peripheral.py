"""Scenarios: Peripheral Device Setup & Diagnostics (audio, headsets, webcams)."""

from __future__ import annotations

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    default_standard_in_work_outcome,
    extract_ticket_text,
)
from shared.domain import DecisionOutcome, FactRequirement, ScenarioDefinition, ScenarioMatch

AUDIO_KEYWORDS = ("колонк", "коллонк", "наушник", "микрофон", "гарнитур", "звук", "динамик")
INSTALL_KEYWORDS = ("установ", "подключ", "настро", "добав")
FAILURE_KEYWORDS = (
    "не подключа", "не работ", "не видит", "не слыш", "нет звука",
    "хрип", "фонит", "тихо", "отходит", "сбоит", "не определя",
    "проблема с", "не находит", "не может найти"
)


def _peripheral_setup_match(context: ScenarioContext) -> tuple[bool, float, str]:
    text = extract_ticket_text(context)
    dev_type = context.facts.valid_value("device_type")
    is_peripheral = dev_type in ("audio", "other") or any(kw in text for kw in AUDIO_KEYWORDS)
    if not is_peripheral:
        return False, 0.0, ""

    has_install = any(kw in text for kw in INSTALL_KEYWORDS)
    has_failure = any(kw in text for kw in FAILURE_KEYWORDS)
    if has_install and not has_failure:
        matched = [kw for kw in INSTALL_KEYWORDS if kw in text]
        return True, 0.95, ",".join(matched)
    return False, 0.0, ""


def _peripheral_diag_match(context: ScenarioContext) -> tuple[bool, float, str]:
    text = extract_ticket_text(context)
    dev_type = context.facts.valid_value("device_type")
    is_peripheral = dev_type in ("audio", "other") or any(kw in text for kw in AUDIO_KEYWORDS)
    if not is_peripheral:
        return False, 0.0, ""

    has_failure = any(kw in text for kw in FAILURE_KEYWORDS)
    if has_failure:
        matched = [kw for kw in FAILURE_KEYWORDS if kw in text]
        return True, 0.95, ",".join(matched)
    return False, 0.0, ""


class PeripheralSetupScenario(Scenario):
    """Сценарий установки и подключения периферийных устройств."""

    definition = ScenarioDefinition(
        key="peripheral_setup",
        version=1,
        risk_level=0,
        allowed_actions=["apply_triage"],
        clarification_outcome_key="peripheral_clarify",
        required_facts=[
            FactRequirement(key="pc_name", clarification_key="clarify_pc_name")
        ],
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        matched, score, reason = _peripheral_setup_match(context)
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=matched,
            score=score,
            reason=reason or None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        return default_standard_in_work_outcome()


class PeripheralDiagnosticsScenario(Scenario):
    """Сценарий диагностики сбоев периферийных устройств (звук, гарнитура, микрофон)."""

    definition = ScenarioDefinition(
        key="peripheral_diagnostics",
        version=1,
        risk_level=0,
        allowed_actions=["apply_triage"],
        clarification_outcome_key="peripheral_clarify",
        required_facts=[
            FactRequirement(key="pc_name", clarification_key="clarify_pc_name")
        ],
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        matched, score, reason = _peripheral_diag_match(context)
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=matched,
            score=score,
            reason=reason or None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        return default_standard_in_work_outcome()


def build_peripheral_scenarios() -> tuple[Scenario, ...]:
    return (
        PeripheralSetupScenario(),
        PeripheralDiagnosticsScenario(),
    )
