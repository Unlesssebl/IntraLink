"""Scenario: Network & Internet Diagnostics (network_diagnostics)."""

from __future__ import annotations

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    default_standard_in_work_outcome,
    extract_ticket_text,
)
from shared.domain import DecisionOutcome, ScenarioDefinition, ScenarioMatch

NETWORK_SERVICE_IDS = {20, 43, 71, 181}

NETWORK_PHRASES = (
    "прерывается интернет",
    "прерывается работа интернета",
    "отваливается сеть",
    "обрыв сети",
    "потери пакетов",
    "потеря пакетов",
    "не работает интернет",
    "пропадает интернет",
    "нет доступа к сети",
    "падает сеть",
    "сетевой сбой",
)

PC_LAG_PHRASES = (
    "медленно грузит",
    "долго грузится",
    "все грузится",
    "тормозит компьютер",
    "зависает пк",
    "виснет комп",
)


class NetworkDiagnosticsScenario(Scenario):
    """Сценарий диагностики сбоев локальной сети и интернета."""

    definition = ScenarioDefinition(
        key="network_diagnostics",
        version=1,
        risk_level=0,
        allowed_actions=["apply_triage"],
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        text = extract_ticket_text(context)
        task = context.task
        sid = task.get("ServiceId") or task.get("service_id")
        if sid is not None and int(sid) in NETWORK_SERVICE_IDS:
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=True,
                score=0.96,
                reason=f"service_id:{sid}",
            )

        found = [p for p in NETWORK_PHRASES if p in text]
        if not found:
            return ScenarioMatch(
                scenario_key=self.definition.key,
                scenario_version=self.definition.version,
                matched=False,
                score=0.0,
                reason=None,
            )

        is_secondary = any(p in text for p in PC_LAG_PHRASES)
        score = 0.80 if is_secondary else 0.95
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=True,
            score=score,
            reason=",".join(found),
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        return default_standard_in_work_outcome()
