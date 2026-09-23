"""Scenario: Offline Host Handling (offline_host)."""

from __future__ import annotations

from typing import Any

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    contains_any,
    default_standard_in_work_outcome,
    extract_ticket_text,
)
from shared.domain import (
    ClarificationRequired,
    DecisionOutcome,
    Evidence,
    FactRequirement,
    NoMatch,
    ScenarioDefinition,
    ScenarioMatch,
)


class OfflineHostScenario(Scenario):
    """Сценарий обработки недоступных по сети рабочих станций (Статус 35 `pc_offline`)."""

    definition = ScenarioDefinition(
        key="offline_host",
        version=1,
        risk_level=0,
        clarification_outcome_key="pc_offline",
        required_facts=[
            FactRequirement(key="pc_name", clarification_key="clarify_pc_name")
        ],
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        text = extract_ticket_text(context)
        matcher = contains_any("не доступ", "недоступ", "offline", "не в сети")
        matched, score, reason = matcher(context)
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=matched,
            score=score,
            reason=reason or None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        diag = context.diagnostics
        task = context.task
        name = str(task.get("Name") or "").lower()
        desc = str(task.get("Description") or "").lower()
        user_text = f"{name} {desc}".strip()

        # Исключения: списание, аппаратные поломки, доставка в 112
        is_hardware = any(w in user_text for w in [
            "не включается", "черный экран", "сгорел", "аппаратный ремонт", "замена диска"
        ])
        is_delivery = any(w in user_text for w in ["112 каб", "каб. 112", "кабинет 112"])
        is_decommission = any(w in user_text for w in ["списание", "списать", "дефектовк"])

        if is_decommission or is_hardware or is_delivery:
            return default_standard_in_work_outcome()

        if diag and diag.get("is_online") is False and diag.get("status") not in ("error", "unknown"):
            target = diag.get("target")
            if target and str(target).strip() and str(target).upper() != "UNKNOWN":
                return ClarificationRequired(
                    rule_key="host.offline",
                    rule_version="2",
                    outcome_key="pc_offline",
                    missing_fields=["pc_availability"],
                    context={"pc_name": str(target)},
                    evidence=[
                        Evidence(
                            source="rule",
                            field="diag.is_online",
                            code="host_offline",
                            detail=f"Workstation {target} is offline",
                        )
                    ],
                )

        return default_standard_in_work_outcome()
