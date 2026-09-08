"""Shadow comparator for verifying DecisionEnvelope against legacy heuristic predictions."""

from typing import Any
from pydantic import BaseModel, Field

from shared.domain import (
    ActionProposed,
    ClarificationRequired,
    DecisionEnvelope,
    ManualReviewRequired,
    NoMatch,
)


class ShadowComparisonResult(BaseModel):
    matched: bool
    diverged: bool
    scenario_matched: bool
    outcome_matched: bool
    divergence_reasons: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


LEGACY_SCENARIO_EQUIVALENCE = {
    "user_creation": {"create_user", "user_creation"},
    "create_user": {"create_user", "user_creation"},
    "printer_installation": {"install_printer", "printer_installation", "redirect", "offline_host"},
    "install_printer": {"install_printer", "printer_installation", "redirect", "offline_host"},
    "grant_wlan": {"grant_wlan"},
    "redirect": {"redirect", "printer_installation"},
    "offline_host": {"offline_host", "printer_installation"},
}


class ShadowComparator:
    """Compares new scenario orchestrator decisions against legacy expectations."""

    @classmethod
    def compare(
        cls,
        *,
        legacy_scenario_key: str | None,
        envelope: DecisionEnvelope,
        task: dict[str, Any],
        comments: list[dict[str, Any]] | None = None,
    ) -> ShadowComparisonResult:
        reasons: list[str] = []
        scenario_matched = True
        outcome_matched = True

        new_scenario = envelope.scenario_key
        comments = comments or []

        # 1. Сравнение соответствия сценария
        if legacy_scenario_key:
            equivalent = LEGACY_SCENARIO_EQUIVALENCE.get(legacy_scenario_key, {legacy_scenario_key})
            if new_scenario not in equivalent:
                scenario_matched = False
                reasons.append(
                    f"scenario_mismatch: legacy={legacy_scenario_key}, new={new_scenario}"
                )
        elif new_scenario and new_scenario != "no_match":
            # В legacy сценарий не был настроен, а новый классификатор определил сценарий
            reasons.append(
                f"legacy_unconfigured_new_detected: new={new_scenario}"
            )

        # 2. Оценка уверенности модели / эвристики
        if envelope.confidence < 0.70:
            reasons.append(f"low_confidence: score={envelope.confidence:.2f}")

        # 3. Сравнение исходов и параметров для ключевых сценариев
        facts = envelope.facts_summary or {}

        if new_scenario in {"create_user", "user_creation"}:
            raw_meta = (task.get("_field_meta") or {}).get("raw") or {}
            # В legacy обязательны фамилия (1057), имя (1058), должность (1065)
            legacy_has_basics = bool(
                raw_meta.get("1057") and raw_meta.get("1058") and raw_meta.get("1065")
            )
            if isinstance(envelope.outcome, ClarificationRequired):
                if legacy_has_basics:
                    outcome_matched = False
                    reasons.append(
                        "outcome_mismatch: legacy had basic fields, orchestrator requested clarification"
                    )
            elif isinstance(envelope.outcome, ActionProposed):
                if not legacy_has_basics:
                    outcome_matched = False
                    reasons.append(
                        "outcome_mismatch: legacy lacked basic fields, orchestrator proposed action"
                    )

        elif new_scenario in {"install_printer", "printer_installation"}:
            raw_pc = facts.get("target_host") or facts.get("pc_name")
            if not raw_pc and isinstance(envelope.outcome, ActionProposed):
                outcome_matched = False
                reasons.append("parameter_missing: target_host is missing in proposed printer install")

        elif isinstance(envelope.outcome, (ManualReviewRequired, NoMatch)):
            if legacy_scenario_key and legacy_scenario_key in {"user_creation", "printer_installation"}:
                reasons.append(
                    f"orchestrator_manual_review: new outcome is {envelope.outcome.kind}"
                )

        diverged = len(reasons) > 0
        matched = not diverged

        details = {
            "legacy_scenario_key": legacy_scenario_key,
            "new_scenario_key": new_scenario,
            "outcome_kind": envelope.outcome.kind,
            "confidence": envelope.confidence,
            "reasons": reasons,
        }

        return ShadowComparisonResult(
            matched=matched,
            diverged=diverged,
            scenario_matched=scenario_matched,
            outcome_matched=outcome_matched,
            divergence_reasons=reasons,
            details=details,
        )
