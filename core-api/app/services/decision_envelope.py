"""Compatibility helpers for the unified decision envelope."""

from __future__ import annotations

from typing import Any

from shared.domain import ActionProposed, DecisionEnvelope


def envelope_to_legacy(envelope: DecisionEnvelope) -> dict[str, Any]:
    policy = envelope.policy or {}
    outcome = envelope.outcome
    result: dict[str, Any] = {
        "template_key": policy.get("template_key")
        or getattr(outcome, "outcome_key", envelope.scenario_key),
        "rule_type": outcome.rule_key,
        "name": policy.get("status_name") or envelope.scenario_key,
        "comment": envelope.response_draft,
        "confidence": envelope.confidence,
        "typed_outcome": outcome.model_dump(mode="json"),
        "_resolution_policy": policy,
        "scenario_key": envelope.scenario_key,
        "scenario_version": envelope.scenario_version,
        "decision_envelope_version": envelope.schema_version,
    }
    for key in ("status_id", "status_name", "expenses"):
        if policy.get(key) is not None:
            result[key] = policy[key]
    if isinstance(outcome, ActionProposed):
        result["action"] = outcome.action
        result["action_parameters"] = outcome.parameters.model_dump(mode="json") if hasattr(
            outcome.parameters, "model_dump"
        ) else outcome.parameters
    metadata = getattr(outcome, "metadata", None)
    if isinstance(metadata, dict):
        result.update(metadata)
    return result
