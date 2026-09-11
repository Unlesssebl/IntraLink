"""Decision execution trace collection for Stage 5 audit.

Captures observed inputs, outputs, rules, and durations across the analysis pipeline.
"""

from __future__ import annotations

from contextlib import contextmanager
import time
from typing import Any, Generator

from app.services.decision_journal import sanitize_payload

TRACE_SCHEMA_VERSION = 1
MAX_TRACE_ITEMS = 50
MAX_TEXT_LEN = 1000


def _truncate_value(val: Any) -> Any:
    if isinstance(val, str):
        if len(val) > MAX_TEXT_LEN:
            return val[:MAX_TEXT_LEN] + "… [truncated]"
        return val
    if isinstance(val, list):
        if len(val) > MAX_TRACE_ITEMS:
            truncated_list = [_truncate_value(x) for x in val[:MAX_TRACE_ITEMS]]
            truncated_list.append(f"… [{len(val) - MAX_TRACE_ITEMS} items truncated]")
            return truncated_list
        return [_truncate_value(x) for x in val]
    if isinstance(val, dict):
        return {k: _truncate_value(v) for k, v in val.items()}
    return val


def sanitize_trace_payload(data: Any) -> Any:
    """Sanitizes sensitive data and truncates oversized payloads for trace steps."""
    cleaned = sanitize_payload(data)
    return _truncate_value(cleaned)


class TraceSpan:
    """Tracks elapsed monotonic time for an execution phase."""

    def __init__(self, trace: DecisionExecutionTrace, component: str, input_data: dict[str, Any] | None = None):
        self.trace = trace
        self.component = component
        self.input_data = input_data or {}
        self.start_time = time.monotonic()
        self.end_time: float | None = None
        self.duration_ms: float | None = None
        self.finished = False

    def finish(
        self,
        *,
        status: str = "succeeded",
        output_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        duration_ms: float | None = None,
    ) -> dict[str, Any]:
        if self.finished:
            return {}
        self.finished = True
        self.end_time = time.monotonic()
        measured_duration = round((self.end_time - self.start_time) * 1000.0, 2)
        final_duration = duration_ms if duration_ms is not None else measured_duration
        return self.trace.record_step(
            component=self.component,
            status=status,
            input_data=self.input_data,
            output_data=output_data,
            metadata=metadata,
            error_code=error_code,
            duration_ms=final_duration,
        )


class DecisionExecutionTrace:
    """Ephemeral per-analysis trace accumulator."""

    VALID_COMPONENTS = {
        "facts",
        "rag",
        "routing",
        "policy",
        "plan",
        "ai",
        "guard",
        "compiler",
    }
    VALID_STATUSES = {"succeeded", "fallback", "failed", "skipped"}

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []

    def start_span(
        self,
        component: str,
        input_data: dict[str, Any] | None = None,
    ) -> TraceSpan:
        return TraceSpan(self, component, input_data)

    @contextmanager
    def span(
        self,
        component: str,
        input_data: dict[str, Any] | None = None,
        default_error_code: str | None = None,
    ) -> Generator[TraceSpan, None, None]:
        s = self.start_span(component, input_data)
        try:
            yield s
        except Exception as exc:
            if not s.finished:
                s.finish(
                    status="failed",
                    error_code=default_error_code or exc.__class__.__name__,
                    output_data={"error": str(exc)[:500]},
                )
            raise

    def record_step(
        self,
        *,
        component: str,
        status: str = "succeeded",
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        duration_ms: float | None = None,
    ) -> dict[str, Any]:
        comp = component if component in self.VALID_COMPONENTS else "compiler"
        st = status if status in self.VALID_STATUSES else "succeeded"

        meta = {"trace_schema_version": TRACE_SCHEMA_VERSION}
        if metadata:
            meta.update(metadata)

        step = {
            "component": comp,
            "status": st,
            "input": sanitize_trace_payload(input_data or {}),
            "output": sanitize_trace_payload(output_data or {}),
            "metadata": sanitize_trace_payload(meta),
            "error_code": error_code,
            "duration_ms": duration_ms if duration_ms is not None else None,
        }
        self._steps.append(step)
        return step

    def to_steps(self) -> list[dict[str, Any]]:
        return list(self._steps)

    def mark_remaining_skipped(self, reason: str = "upstream_failure") -> None:
        recorded_comps = {s["component"] for s in self._steps}
        pipeline_order = ["facts", "rag", "routing", "policy", "plan", "ai", "guard", "compiler"]
        for comp in pipeline_order:
            if comp not in recorded_comps:
                self.record_step(
                    component=comp,
                    status="skipped",
                    metadata={"skip_reason": reason},
                    duration_ms=None,
                )
