"""Persistent, PII-free lifecycle state for an operator-facing AI suggestion."""

import datetime as dt
import hashlib
import json
from typing import Any

from app.services.actions import get_policy_engine

SUGGESTION_KEY_PREFIX = "ai:suggestion:"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _decode(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None


def action_for_decision(decision: dict[str, Any] | None) -> str:
    """Map a rule outcome to the action whose policy the operator must see."""
    rule_type = (decision or {}).get("rule_type")
    return {
        "wlan_access": "grant_wlan",
        "user_creation": "create_user",
    }.get(rule_type, "apply_triage")


def missing_data_for_decision(
    task: dict[str, Any], decision: dict[str, Any] | None, action_id: str
) -> list[str]:
    """Return field labels only; never put ticket text or identities into state."""
    meta = task.get("_field_meta") or {}
    if action_id == "grant_wlan":
        identity = (
            task.get("CreatorLogin")
            or task.get("CreatorEmail")
            or task.get("creator_login")
            or meta.get("login")
        )
        return [] if identity else ["логин или UPN заявителя"]
    if action_id == "create_user":
        required = {
            "фамилия": (decision or {}).get("surname"),
            "имя": (decision or {}).get("first_name") or (decision or {}).get("name"),
            "подразделение": (decision or {}).get("department"),
        }
        return [label for label, value in required.items() if not value]
    if action_id == "install_printer" and not (meta.get("pc_name") or task.get("PcName")):
        return ["имя целевого ПК"]
    return []


def snapshot_fingerprint(
    task: dict[str, Any], history: Any, decision: dict[str, Any] | None
) -> str:
    """Hash only current operational inputs; the hash itself is safe to expose."""
    payload = {
        "name": task.get("Name"),
        "description": task.get("Description"),
        "service_id": task.get("ServiceId"),
        "status_id": task.get("StatusId"),
        "history": history or [],
        "decision": decision or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


async def build_suggestion_state(
    *,
    redis_client: Any,
    task_id: int,
    task: dict[str, Any],
    history: Any,
    decision: dict[str, Any] | None,
    force_recalculate: bool = False,
) -> dict[str, Any]:
    """Create a current suggestion once, or retain/mark a stale one.

    A stale suggestion is intentionally not silently replaced during a normal
    card refresh.  That makes the operator explicitly request a recalculation.
    """
    fingerprint = snapshot_fingerprint(task, history, decision)
    key = f"{SUGGESTION_KEY_PREFIX}{task_id}"
    existing = _decode(await redis_client.get(key))
    if existing and not force_recalculate:
        if existing.get("state") == "stale":
            return existing
        if existing.get("fingerprint") != fingerprint:
            existing.update(
                {
                    "state": "stale",
                    "stale_at": _now(),
                    "stale_reason": "Заявка изменилась после расчёта рекомендации.",
                }
            )
            await redis_client.set(key, json.dumps(existing, ensure_ascii=False), ex=3600 * 24)
            return existing
        return existing

    action_id = action_for_decision(decision)
    mode, allowed, policy_reason = await get_policy_engine().evaluate_execution_mode(
        action_id=action_id,
        requested_mode="auto",
        redis_client=redis_client,
    )
    missing_data = missing_data_for_decision(task, decision, action_id)
    state = {
        "task_id": task_id,
        "state": "current",
        "fingerprint": fingerprint,
        "source": "Rule Engine + AI Hub/RAG",
        "calculated_at": _now(),
        "policy": {
            "action": action_id,
            "mode": mode,
            "allowed": allowed,
            "blocked": not allowed,
            "reason": policy_reason,
        },
        "missing_data": missing_data,
    }
    await redis_client.set(key, json.dumps(state, ensure_ascii=False), ex=3600 * 24)
    return state


async def invalidate_suggestion(
    redis_client: Any, task_id: int, reason: str = "Заявка вручную изменена оператором."
) -> dict[str, Any] | None:
    key = f"{SUGGESTION_KEY_PREFIX}{task_id}"
    try:
        state = _decode(await redis_client.get(key))
        if not state:
            return None
        state.update({"state": "stale", "stale_at": _now(), "stale_reason": reason})
        await redis_client.set(key, json.dumps(state, ensure_ascii=False), ex=3600 * 24)
        return state
    except Exception:
        # A successful manual IntraService update must never be reported as a
        # failure merely because optional recommendation state expired/unavailable.
        return None


async def require_current_suggestion(
    redis_client: Any, task_id: int, fingerprint: str | None
) -> dict[str, Any]:
    state = _decode(await redis_client.get(f"{SUGGESTION_KEY_PREFIX}{task_id}"))
    if not state or state.get("state") != "current":
        raise ValueError("AI-предложение неактуально; выполните повторный расчёт.")
    if not fingerprint or fingerprint != state.get("fingerprint"):
        raise ValueError("Версия AI-предложения не совпадает с текущей; выполните повторный расчёт.")
    if state.get("policy", {}).get("blocked"):
        raise ValueError(state["policy"].get("reason") or "Действие заблокировано policy.")
    if state.get("missing_data"):
        raise ValueError("Не заполнены данные: " + ", ".join(state["missing_data"]))
    return state
