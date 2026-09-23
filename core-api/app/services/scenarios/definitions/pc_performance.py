"""Scenario: PC Performance Diagnostics (pc_performance)."""

from __future__ import annotations

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    default_standard_in_work_outcome,
    extract_ticket_text,
)
from shared.domain import DecisionOutcome, ScenarioDefinition, ScenarioMatch

PERFORMANCE_PHRASES = (
    "медленно грузит",
    "долго грузится",
    "очень долго",
    "все грузится",
    "тормозит компьютер",
    "медленно работает",
    "зависает компьютер",
    "зависает пк",
    "зависает весь пк",
    "виснет пк",
    "виснет компьютер",
    "виснет комп",
    "сильно виснет",
    "невозможно работать,тормозит",
    "программы могут загружаться",
    "компьютер медленно",
)


class PCPerformanceScenario(Scenario):
    """Сценарий диагностики медленной работы или зависания ПК."""

    definition = ScenarioDefinition(
        key="pc_performance",
        version=1,
        risk_level=0,
        allowed_actions=["apply_triage"],
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        text = extract_ticket_text(context)
        task = context.task
        sid = task.get("ServiceId") or task.get("service_id")
        if sid is not None and str(sid) == "59":
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=True,
                score=0.96,
                reason="service_id:59",
            )

        found = [p for p in PERFORMANCE_PHRASES if p in text]
        if found:
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=True,
                score=0.95,
                reason=",".join(found),
            )

        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=False,
            score=0.0,
            reason=None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        return default_standard_in_work_outcome()
