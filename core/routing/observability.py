"""Structured Observability and Audit Logging for Evidence-Based Routing Cascade.

Emits sanitized structured logs without any PII, passwords, applicant texts, or raw prompts.
"""

import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger("core.routing.observability")


def emit_routing_event(
    event_name: str,
    *,
    task_id: int,
    decision_id: Optional[uuid.UUID] = None,
    plan_id: Optional[uuid.UUID] = None,
    command_id: Optional[uuid.UUID] = None,
    scenario_key: Optional[str] = None,
    routing_state: Optional[str] = None,
    router_version: str = "2.0.0",
    prompt_version: Optional[str] = None,
    duration_ms: Optional[float] = None,
    reason_codes: Optional[List[str]] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit a sanitized structured routing audit log entry."""
    payload: Dict[str, Any] = {
        "event": event_name,
        "task_id": task_id,
        "decision_id": str(decision_id) if decision_id else None,
        "plan_id": str(plan_id) if plan_id else None,
        "command_id": str(command_id) if command_id else None,
        "scenario_key": scenario_key,
        "routing_state": routing_state,
        "router_version": router_version,
        "prompt_version": prompt_version,
        "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
        "reason_codes": reason_codes or [],
    }
    if extra_meta:
        for k, v in extra_meta.items():
            if k not in ("password", "token", "auth_b64", "secret", "prompt", "comment"):
                payload[k] = v

    logger.info("ROUTING_EVENT [%s] %s", event_name, payload, extra={"structured_event": payload})


@contextmanager
def timed_routing_block(
    event_name: str,
    *,
    task_id: int,
    scenario_key: Optional[str] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Context manager measuring execution duration and emitting completion event."""
    start_time = time.perf_counter()
    ctx: Dict[str, Any] = {}
    try:
        yield ctx
        duration_ms = (time.perf_counter() - start_time) * 1000
        emit_routing_event(
            f"{event_name}_completed",
            task_id=task_id,
            decision_id=ctx.get("decision_id"),
            plan_id=ctx.get("plan_id"),
            scenario_key=scenario_key or ctx.get("scenario_key"),
            routing_state=ctx.get("routing_state"),
            duration_ms=duration_ms,
            reason_codes=ctx.get("reason_codes"),
        )
    except Exception as exc:
        duration_ms = (time.perf_counter() - start_time) * 1000
        emit_routing_event(
            f"{event_name}_failed",
            task_id=task_id,
            decision_id=ctx.get("decision_id"),
            plan_id=ctx.get("plan_id"),
            scenario_key=scenario_key or ctx.get("scenario_key"),
            duration_ms=duration_ms,
            reason_codes=["exception_raised"],
            extra_meta={"error_type": type(exc).__name__},
        )
        raise
