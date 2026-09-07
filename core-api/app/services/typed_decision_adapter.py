"""Compatibility boundary from typed domain outcomes to the legacy UI shape."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.resolution_service import ResolutionUnavailable, resolve_outcome


async def materialize_typed_decision(
    db: AsyncSession, decision: dict[str, Any]
) -> dict[str, Any]:
    typed = decision.get("typed_outcome")
    if not isinstance(typed, dict) or decision.get("_resolution_policy"):
        return decision
    outcome_key = typed.get("outcome_key")
    kind = typed.get("kind")
    if not outcome_key or kind not in {"clarification", "action", "resolution"}:
        return decision
    context = typed.get("context") or {}
    try:
        resolution = await resolve_outcome(
            db,
            outcome_key,
            context,
            expected_kind=kind,
            expected_action=typed.get("action") if kind == "action" else None,
        )
    except ResolutionUnavailable as exc:
        decision.update(
            {
                "status_id": 35,
                "status_name": "Требует уточнения",
                "comment": "Решение требует ручной проверки: конфигурация ответа недоступна.",
                "_resolution_error": str(exc),
            }
        )
        decision.pop("action", None)
        decision.pop("action_parameters", None)
        return decision

    decision.update(
        {
            "template_key": resolution["template_key"],
            "comment": resolution["comment"],
            "expenses": resolution["expenses"],
            "_resolution_policy": resolution,
        }
    )
    if resolution["status_id"] is not None:
        decision["status_id"] = resolution["status_id"]
        decision["status_name"] = resolution["status_name"]
    elif typed.get("target_status_id") is not None:
        decision["status_id"] = typed["target_status_id"]

    # Forward rich metadata (is_redirect, target_service_name, markers, etc.) for UI/filter compatibility
    if isinstance(typed.get("metadata"), dict):
        decision.update(typed["metadata"])
    return decision
