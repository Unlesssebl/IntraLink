"""Scenario: Generic Helpdesk Consultation Fallback (consultation)."""

from __future__ import annotations

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import default_standard_in_work_outcome
from shared.domain import DecisionOutcome, ScenarioDefinition, ScenarioMatch


class ConsultationScenario(Scenario):
    """Стандартный сценарий общего приема заявки 1-й линией поддержки (Fallback)."""

    definition = ScenarioDefinition(
        key="consultation",
        version=1,
        risk_level=0,
        allowed_actions=["apply_triage"],
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=True,
            score=0.01,
            reason="deterministic_fallback",
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        return default_standard_in_work_outcome()
