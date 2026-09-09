"""Compatibility helpers for the unified decision envelope."""

from __future__ import annotations

from typing import Any

from shared.domain import ActionProposed, DecisionEnvelope, get_scenario_display_name


def envelope_to_legacy(envelope: DecisionEnvelope) -> dict[str, Any]:
    policy = envelope.policy or {}
    outcome = envelope.outcome
    scenario_display_name = get_scenario_display_name(
        envelope.scenario_key, version=envelope.scenario_version
    )
    result: dict[str, Any] = {
        "template_key": policy.get("template_key")
        or getattr(outcome, "outcome_key", envelope.scenario_key),
        "rule_type": outcome.rule_key,
        "name": policy.get("status_name") or scenario_display_name,
        "comment": envelope.response_draft,
        "confidence": envelope.confidence,
        "typed_outcome": outcome.model_dump(mode="json"),
        "_resolution_policy": policy,
        "scenario_key": envelope.scenario_key,
        "scenario_title": scenario_display_name,
        "scenario_short_title": get_scenario_display_name(envelope.scenario_key, short=True),
        "scenario_version": envelope.scenario_version,
        "decision_envelope_version": envelope.schema_version,
    }

    for key in ("status_id", "status_name", "expenses"):
        if policy.get(key) is not None:
            result[key] = policy[key]
    target_status_id = (
        policy.get("target_status_id")
        or policy.get("status_id")
        or result.get("status_id")
        or getattr(outcome, "target_status_id", None)
    )
    if target_status_id is None:
        kind = getattr(outcome, "kind", None)
        if kind == "clarification":
            target_status_id = 35
        elif kind == "resolution" and (
            envelope.scenario_key == "redirect"
            or getattr(outcome, "outcome_key", "") == "service_redirect"
        ):
            target_status_id = 30
        else:
            target_status_id = 27

    result["target_status_id"] = target_status_id
    result["status_id"] = target_status_id

    status_name_map = {
        26: "Новая",
        27: "В работе",
        29: "Выполнена",
        30: "Отменена",
        35: "Запрос информации",
        48: "Пауза",
    }
    target_status_name = (
        policy.get("target_status_name")
        or policy.get("status_name")
        or result.get("status_name")
        or getattr(outcome, "target_status_name", None)
        or status_name_map.get(target_status_id, "В работе")
    )
    result["target_status_name"] = target_status_name
    result["status_name"] = target_status_name

    if "expenses" not in result or result["expenses"] is None:
        result["expenses"] = policy.get("expenses") or (5 if target_status_id in (30, 35) else 10)

    # Fallback comment if policy failed to render
    if policy.get("resolution_error") or not result.get("comment"):
        fallback_comment = (
            getattr(outcome, "comment", None)
            or getattr(outcome, "explanation", None)
            or (getattr(outcome, "context", {}).get("comment") if hasattr(outcome, "context") else None)
        )
        if fallback_comment:
            result["comment"] = fallback_comment

    if isinstance(outcome, ActionProposed):
        result["action"] = outcome.action
        result["action_parameters"] = outcome.parameters.model_dump(mode="json") if hasattr(
            outcome.parameters, "model_dump"
        ) else outcome.parameters
    metadata = getattr(outcome, "metadata", None)
    if isinstance(metadata, dict):
        result.update(metadata)
    if "is_redirect" not in result:
        result["is_redirect"] = bool(
            getattr(outcome, "is_redirect", False)
            or outcome.rule_key == "rule.service_redirect"
            or getattr(outcome, "outcome_key", "") == "service_redirect"
            or envelope.scenario_key == "redirect"
        )
    return result
