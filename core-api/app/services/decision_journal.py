"""Durable decision journal shared by manual triage and autopilot."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database.db import DecisionFeedback, DecisionRecord, DecisionStep
from app.services.ai_suggestions import action_for_decision, missing_data_for_decision
from shared.domain import DecisionEnvelope


SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|authorization|credential)", re.I
)
SECRET_TEXT_RE = re.compile(
    r"(?i)\b(password|пароль|token|токен|secret)\s*[:=]\s*([^\s,;]+)"
)
MAX_TEXT_LENGTH = 8_000


def sanitize_payload(value: Any) -> Any:
    """Remove obvious secrets and bound text copied from an external ticket."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if SECRET_KEY_RE.search(str(key))
            else sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value[:200]]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value[:200]]
    if isinstance(value, str):
        cleaned = SECRET_TEXT_RE.sub(
            lambda match: f"{match.group(1)}: [REDACTED]", value
        )
        if len(cleaned) > MAX_TEXT_LENGTH:
            return cleaned[:MAX_TEXT_LENGTH] + "… [truncated]"
        return cleaned
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_TEXT_LENGTH]


def context_fingerprint(
    *,
    task: dict[str, Any],
    history: list[dict[str, Any]],
    decision: dict[str, Any] | None,
    analysis_kind: str,
) -> str:
    material = {
        "analysis_kind": analysis_kind,
        "task": task,
        "history": history,
        "decision": decision or {},
    }
    encoded = json.dumps(
        sanitize_payload(material), ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_fingerprint_task_field(key: str, val: Any) -> Any:
    if key == "ExecutorIds":
        if not val:
            return ""
        if isinstance(val, str):
            parts = [p.strip() for p in val.split(",") if p.strip()]
            return ",".join(sorted(parts))
        if isinstance(val, (list, tuple, set)):
            parts = [str(p).strip() for p in val if str(p).strip()]
            return ",".join(sorted(parts))
        return str(val).strip()
    if key == "ExecutorId":
        if val is None or val == "" or val == 0:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
    if key in ("Name", "Description"):
        return "" if val is None else str(val).strip()
    if key in ("Attachments", "attachments"):
        if not val:
            return None
    if key == "CustomFields":
        if not val:
            return []
    return val


def ticket_snapshot_fingerprint(
    task: dict[str, Any], history: list[dict[str, Any]] | None = None
) -> str:
    """Fingerprint only mutable external ticket data, independent of the proposal."""
    task_keys = (
        "Id",
        "StatusId",
        "ServiceId",
        "ExecutorId",
        "ExecutorIds",
        "Name",
        "Description",
        "Changed",
        "CustomFields",
        "Attachments",
        "attachments",
    )
    history_keys = (
        "Id",
        "id",
        "Comments",
        "Comment",
        "Text",
        "EditorId",
        "Editor",
        "Created",
        "Date",
        "Changed",
    )
    task_material = {}
    for key in task_keys:
        if key in task or key == "ExecutorIds":
            task_material[key] = _normalize_fingerprint_task_field(key, task.get(key))
    material = {
        "task": task_material,
        "history": [
            {key: item.get(key) for key in history_keys if key in item}
            for item in (history or [])
            if isinstance(item, dict)
        ],
    }
    encoded = json.dumps(
        sanitize_payload(material), ensure_ascii=False, sort_keys=True, default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def triage_context_fingerprint(
    task: dict[str, Any], history: list[dict[str, Any]] | None = None
) -> str:
    """Stable identity of the source snapshot and the logic used to analyse it."""
    material = {
        "ticket_fingerprint": ticket_snapshot_fingerprint(task, history),
        "analysis_revision": settings.ANALYSIS_REVISION,
        "analysis_kind": "triage",
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def analysis_state(
    record: DecisionRecord | None,
    *,
    task: dict[str, Any] | None = None,
    history: list[dict[str, Any]] | None = None,
    freshness_known: bool = True,
    applied: bool = False,
    last_attempt: DecisionRecord | None = None,
) -> dict[str, Any]:
    """Public, operator-facing analysis state derived from durable data."""
    failed_attempt = bool(
        last_attempt
        and last_attempt.status == "failed"
        and (record is None or last_attempt.version > record.version)
    )
    attempt_payload = None
    if last_attempt is not None:
        attempt_payload = {
            "state": "failed" if last_attempt.status == "failed" else "succeeded",
            "error_code": (last_attempt.envelope_json or {}).get("error_code"),
            "finished_at": (
                last_attempt.finalized_at or last_attempt.created_at
            ).isoformat()
            if (last_attempt.finalized_at or last_attempt.created_at)
            else None,
        }
    if record is None:
        return {
            "has_result": False,
            "state": "failed" if failed_attempt else "not_analyzed",
            "freshness": "unknown" if not freshness_known else "current",
            "disposition": "available",
            "decision_id": None,
            "decision_version": None,
            "scenario_key": None,
            "analyzed_at": None,
            "stale_reason": None,
            "can_quick_apply": False,
            "blocked_reason": (
                "Последняя попытка анализа завершилась ошибкой"
                if failed_attempt
                else "Заявка ещё не проанализирована AI"
            ),
            "last_attempt": attempt_payload,
        }

    context = record.context_json or {}
    envelope = record.envelope_json or {}
    gates = envelope.get("gates") or {}
    response = envelope.get("response") or {}
    ready = (
        record.status == "finalized"
        and response.get("state") in {"valid", "fallback"}
        and bool(gates.get("can_send_response") or gates.get("can_execute_action"))
    )
    stale_reason = None
    if not freshness_known or task is None:
        freshness = "unknown"
    elif context.get("analysis_revision") != settings.ANALYSIS_REVISION:
        freshness = "stale"
        stale_reason = "Правила анализа изменились"
    elif history is None:
        expected_task_fingerprint = context.get("ticket_fingerprint_task")
        if expected_task_fingerprint != ticket_snapshot_fingerprint(task, []):
            freshness = "stale"
            stale_reason = "Заявка изменилась в IntraService после анализа"
        else:
            freshness = "current"
    elif context.get("ticket_fingerprint") != ticket_snapshot_fingerprint(
        task, history
    ):
        freshness = "stale"
        stale_reason = "Заявка изменилась в IntraService после анализа"
    else:
        freshness = "current"

    disposition = "applied" if applied else "available"
    can_apply = (
        ready
        and not failed_attempt
        and freshness == "current"
        and disposition == "available"
    )
    blocked_reason = None
    if not can_apply:
        if failed_attempt:
            blocked_reason = "Последняя попытка повторного анализа завершилась ошибкой"
        elif disposition == "applied":
            blocked_reason = "Решение уже применено"
        elif freshness == "unknown":
            blocked_reason = "Актуальность результата не удалось проверить"
        elif freshness == "stale":
            blocked_reason = stale_reason
        elif not ready:
            blocked_reason = "Результат требует ручной проверки"

    return {
        "has_result": True,
        "state": "failed" if failed_attempt or not ready else "ready",
        "freshness": freshness,
        "disposition": disposition,
        "decision_id": str(record.id),
        "decision_version": record.version,
        "scenario_key": envelope.get("scenario_key"),
        "analyzed_at": (record.finalized_at or record.created_at).isoformat()
        if (record.finalized_at or record.created_at)
        else None,
        "stale_reason": stale_reason,
        "can_quick_apply": can_apply,
        "blocked_reason": blocked_reason,
        "last_attempt": attempt_payload,
    }


def _history_context(
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Any]]:
    meaningful = [
        item
        for item in history
        if str(
            item.get("Comments") or item.get("Comment") or item.get("Text") or ""
        ).strip()
    ]
    used = meaningful[-5:]
    omitted = [item.get("Id") or item.get("id") for item in meaningful[:-5]]
    return used, omitted


def serialize_decision(
    record: DecisionRecord, *, steps: list[DecisionStep] | None = None
) -> dict[str, Any]:
    payload = {
        "id": str(record.id),
        "task_id": record.task_id,
        "ticket_run_id": str(record.ticket_run_id) if record.ticket_run_id else None,
        "previous_decision_id": str(record.previous_decision_id)
        if record.previous_decision_id
        else None,
        "version": record.version,
        "analysis_kind": record.analysis_kind,
        "status": record.status,
        "outcome": record.outcome,
        "fingerprint": record.context_fingerprint,
        "context": record.context_json,
        "decision_envelope": record.envelope_json,
        "created_by": record.created_by,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "finalized_at": record.finalized_at.isoformat()
        if record.finalized_at
        else None,
    }
    if steps is not None:
        payload["steps"] = [
            {
                "id": str(step.id),
                "sequence": step.sequence,
                "component": step.component,
                "status": step.status,
                "input": step.input_json,
                "output": step.output_json,
                "metadata": step.metadata_json,
                "error_code": step.error_code,
                "duration_ms": step.duration_ms,
                "input_tokens": step.input_tokens,
                "output_tokens": step.output_tokens,
                "created_at": step.created_at.isoformat() if step.created_at else None,
            }
            for step in steps
        ]
    return payload


VALID_FEEDBACK_REASONS = {
    "wrong_scenario",
    "wrong_status",
    "edited_comment",
    "device_not_found",
    "ungrounded_rag",
    "other",
}

VALID_FEEDBACK_VERDICTS = {
    "accepted",
    "modified",
    "rejected",
    "commented",
}

VALID_FEEDBACK_SOURCES = {
    "recommendation_apply",
    "explicit_feedback",
    "manual_apply",
    "legacy",
}

VALID_REASON_SOURCES = {
    "operator",
    "inferred",
    "none",
    "legacy",
}

LEGACY_FEEDBACK_VERDICTS = {
    "correct",
    "partial",
    "partially_correct",
    "incorrect",
    "insufficient_data",
}

LEGACY_FEEDBACK_REASONS = {
    "wrong_rule",
    "wrong_comment",
    "wrong_status",
    "not_applicable",
    "other",
}


def levenshtein_distance(s1: str, s2: str, max_len: int = 10000) -> int:
    s1 = s1[:max_len]
    s2 = s2[:max_len]
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1] + [0] * len(s2)
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row[j + 1] = min(insertions, deletions, substitutions)
        previous_row = current_row
    return previous_row[-1]


def normalize_comment_text(text: str | None) -> str | None:
    if text is None:
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def calculate_comment_diff(
    proposed: str | None, applied: str | None, *, max_len: int = 10000
) -> dict[str, Any]:
    if proposed is None:
        return {
            "comparable": False,
            "algorithm_version": 1,
            "identical": False,
            "ratio": None,
            "proposed_length": None,
            "applied_length": len(applied) if applied is not None else None,
            "levenshtein_distance": None,
        }
    norm_proposed = normalize_comment_text(proposed) or ""
    norm_applied = normalize_comment_text(applied) or ""
    if norm_proposed == norm_applied:
        return {
            "comparable": True,
            "algorithm_version": 1,
            "identical": True,
            "ratio": 0.0,
            "proposed_length": len(norm_proposed),
            "applied_length": len(norm_applied),
            "levenshtein_distance": 0,
        }
    dist = levenshtein_distance(norm_proposed, norm_applied, max_len=max_len)
    max_l = max(len(norm_proposed), len(norm_applied), 1)
    ratio = round(dist / max_l, 4)
    return {
        "comparable": True,
        "algorithm_version": 1,
        "identical": False,
        "ratio": ratio,
        "proposed_length": len(norm_proposed),
        "applied_length": len(norm_applied),
        "levenshtein_distance": dist,
    }


class DecisionJournalService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def next_identity(self, task_id: int) -> tuple[uuid.UUID, int]:
        previous_version = await self.db.scalar(
            select(func.max(DecisionRecord.version)).where(
                DecisionRecord.task_id == task_id
            )
        )
        return uuid.uuid4(), int(previous_version or 0) + 1

    async def record_envelope(
        self,
        *,
        task_id: int,
        task: dict[str, Any],
        history: list[dict[str, Any]] | None,
        envelope: DecisionEnvelope,
        steps: list[dict[str, Any]] | None = None,
        ticket_run_id: uuid.UUID | None = None,
        analysis_fence: int | None = None,
        actor: str = "system:triage",
        force: bool = False,
        commit: bool = True,
    ) -> DecisionRecord:
        history = history or []
        fingerprint = triage_context_fingerprint(task, history)
        if not force:
            existing = await self.db.scalar(
                select(DecisionRecord).where(
                    DecisionRecord.task_id == task_id,
                    DecisionRecord.context_fingerprint == fingerprint,
                    DecisionRecord.analysis_kind == "triage",
                )
            )
            if existing is not None:
                return existing

        previous = await self.db.scalar(
            select(DecisionRecord)
            .where(DecisionRecord.task_id == task_id)
            .order_by(DecisionRecord.version.desc())
            .limit(1)
        )
        used_history, omitted_history = _history_context(history)
        attachments = task.get("Attachments") or task.get("attachments") or []
        if isinstance(envelope, dict):
            raw_id = envelope.get("decision_id")
            dec_ver = int(envelope.get("decision_version", 1))
            an_state = envelope.get("analysis_state", "finalized")
            outcome_val = envelope.get("outcome") or {}
            outcome_kind = outcome_val.get("action_type") or outcome_val.get("kind") or "standard"
            envelope_dump = envelope
        else:
            raw_id = envelope.decision_id
            dec_ver = envelope.decision_version
            an_state = envelope.analysis_state
            outcome_kind = envelope.outcome.kind
            envelope_dump = envelope.model_dump(mode="json")

        record_id = uuid.UUID(str(raw_id)) if raw_id else uuid.uuid4()
        record = DecisionRecord(
            id=record_id,
            task_id=task_id,
            ticket_run_id=ticket_run_id,
            previous_decision_id=previous.id if previous else None,
            version=dec_ver,
            analysis_kind="triage",
            status=("failed" if an_state == "system_error" else "finalized"),
            outcome=outcome_kind,
            context_fingerprint=(
                hashlib.sha256(f"{fingerprint}:forced:{record_id}".encode()).hexdigest()
                if force
                else fingerprint
            ),
            context_json=sanitize_payload(
                {
                    "task": task,
                    "ticket_fingerprint": ticket_snapshot_fingerprint(task, history),
                    "ticket_fingerprint_task": ticket_snapshot_fingerprint(task, []),
                    "analysis_revision": settings.ANALYSIS_REVISION,
                    "analysis_fence": analysis_fence,
                    "history_used": used_history,
                    "history_omitted_ids": omitted_history,
                    "history_limit": 5,
                    "attachments": [
                        {
                            "id": item.get("Id") or item.get("id"),
                            "name": item.get("Name") or item.get("name"),
                            "state": "available",
                        }
                        for item in attachments
                        if isinstance(item, dict)
                    ],
                }
            ),
            envelope_json=sanitize_payload(envelope_dump),
            created_by=actor,
            finalized_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(record)
        await self.db.flush()
        resolved_steps = (
            steps
            if steps is not None
            else (
                envelope_dump.get("steps")
                if isinstance(envelope_dump, dict)
                else getattr(envelope, "steps", None)
            )
        ) or []
        for sequence, item in enumerate(resolved_steps, start=1):
            comp = (
                item.get("component")
                or item.get("step_name")
                or item.get("phase")
                or "pipeline"
            )
            self.db.add(
                DecisionStep(
                    decision_id=record.id,
                    sequence=sequence,
                    component=str(comp)[:24],
                    status=str(item.get("status") or "succeeded")[:24],
                    input_json=sanitize_payload(item.get("input") or item.get("details") or {}),
                    output_json=sanitize_payload(item.get("output") or {}),
                    metadata_json=sanitize_payload(item.get("metadata") or {}),
                    error_code=item.get("error_code"),
                    duration_ms=item.get("duration_ms"),
                )
            )
        try:
            if commit:
                await self.db.commit()
            else:
                await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            existing = await self.db.scalar(
                select(DecisionRecord).where(
                    DecisionRecord.task_id == task_id,
                    DecisionRecord.context_fingerprint == fingerprint,
                    DecisionRecord.analysis_kind == "triage",
                )
            )
            if existing is None:
                raise
            return existing
        if commit:
            await self.db.refresh(record)
        return record

    async def record_triage(
        self,
        *,
        task_id: int,
        task: dict[str, Any],
        history: list[dict[str, Any]] | None,
        decision: dict[str, Any] | None,
        kb_matches: list[dict[str, Any]] | None = None,
        ai_text: str | None = None,
        ai_metadata: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        ticket_run_id: uuid.UUID | None = None,
        analysis_fence: int | None = None,
        actor: str = "system:triage",
        force: bool = False,
    ) -> DecisionRecord:
        history = history or []
        kb_matches = kb_matches or []
        fingerprint = triage_context_fingerprint(task, history)
        if force:
            fingerprint = hashlib.sha256(
                f"{fingerprint}:forced:{uuid.uuid4()}".encode("utf-8")
            ).hexdigest()
        if not force:
            existing = await self.db.scalar(
                select(DecisionRecord).where(
                    DecisionRecord.task_id == task_id,
                    DecisionRecord.context_fingerprint == fingerprint,
                    DecisionRecord.analysis_kind == "triage",
                )
            )
            if existing is not None:
                return existing

        previous = await self.db.scalar(
            select(DecisionRecord)
            .where(DecisionRecord.task_id == task_id)
            .order_by(DecisionRecord.version.desc())
            .limit(1)
        )
        version = (previous.version if previous else 0) + 1
        used_history, omitted_history = _history_context(history)
        attachments = task.get("Attachments") or task.get("attachments") or []
        rule_trace = list((decision or {}).get("_rule_trace") or [])
        rule_errors = [item for item in rule_trace if item.get("status") == "error"]
        rule_type = (decision or {}).get("rule_type")
        rule_source = bool(decision and rule_type not in {None, "standard_in_work"})
        rag_source = bool(kb_matches or (decision or {}).get("rag_applied"))
        ai_source = (ai_metadata or {}).get("ai_used") is True
        action = action_for_decision(decision)
        typed_outcome = (decision or {}).get("typed_outcome")
        missing_data = missing_data_for_decision(task, decision, action)
        blocked_reasons = list(missing_data)
        limitations: list[str] = []
        if attachments:
            limitations.append("Содержимое вложений не анализировалось")
        if rule_errors:
            blocked_reasons.append("Одно или несколько правил завершились ошибкой")
        outcome = "proposal" if decision else "no_solution"
        envelope_data = (decision or {}).get("_decision_envelope")
        if not isinstance(envelope_data, dict):
            envelope_data = {
                "scenario_key": (decision or {}).get("rule_type") or "legacy_triage",
                "scenario_version": 1,
                "outcome": {
                    "kind": (
                        "resolution"
                        if (decision or {}).get("status_id") == 29
                        else "action"
                        if action
                        else "clarification"
                    ),
                    "action": action,
                    "target_status_id": (decision or {}).get("status_id"),
                },
                "policy": sanitize_payload(policy or {}),
                "response": {
                    "text": ai_text or (decision or {}).get("comment") or "",
                    "state": "valid" if decision else "invalid",
                },
                "gates": {
                    "can_send_response": bool(decision),
                    "can_execute_action": bool(action or decision),
                    "requires_approval": bool((policy or {}).get("requires_approval")),
                    "blocked_reasons": blocked_reasons,
                },
                "analysis_state": "succeeded" if decision else "manual_review",
                "status": "proposed" if decision else "manual_review",
                "source": {
                    "rule": rule_source,
                    "rag": rag_source,
                    "ai": ai_source,
                },
                "completeness": {
                    "complete": not missing_data and not rule_errors,
                    "missing_data": missing_data,
                    "blocked_reasons": blocked_reasons,
                    "limitations": limitations,
                    "history_total": len(history),
                    "history_used": len(used_history),
                    "attachments_total": len(attachments),
                    "attachments_read": 0,
                },
            }
        else:
            envelope_data.setdefault("gates", {
                "can_send_response": bool(decision),
                "can_execute_action": bool(action or decision),
                "requires_approval": bool((policy or {}).get("requires_approval")),
                "blocked_reasons": blocked_reasons,
            })
            envelope_data.setdefault("response", {
                "text": ai_text or (decision or {}).get("comment") or "",
                "state": "valid" if decision else "invalid",
            })
            envelope_data.setdefault("source", {
                "rule": rule_source,
                "rag": rag_source,
                "ai": ai_source,
            })
            envelope_data.setdefault("completeness", {
                "complete": not missing_data and not rule_errors,
                "missing_data": missing_data,
                "blocked_reasons": blocked_reasons,
                "limitations": limitations,
                "history_total": len(history),
                "history_used": len(used_history),
                "attachments_total": len(attachments),
                "attachments_read": 0,
            })

        record = DecisionRecord(
            task_id=task_id,
            ticket_run_id=ticket_run_id,
            previous_decision_id=previous.id if previous else None,
            version=version,
            analysis_kind="triage",
            status="finalized",
            outcome=outcome,
            context_fingerprint=fingerprint,
            context_json=sanitize_payload(
                {
                    "task": task,
                    "ticket_fingerprint": ticket_snapshot_fingerprint(task, history),
                    "ticket_fingerprint_task": ticket_snapshot_fingerprint(task, []),
                    "analysis_revision": settings.ANALYSIS_REVISION,
                    "analysis_fence": analysis_fence,
                    "history_used": used_history,
                    "history_omitted_ids": omitted_history,
                    "history_limit": 5,
                    "attachments": [
                        {
                            "id": item.get("Id") or item.get("id"),
                            "name": item.get("Name") or item.get("name"),
                            "state": "not_read",
                        }
                        for item in attachments
                        if isinstance(item, dict)
                    ],
                }
            ),
            envelope_json=envelope_data,
            created_by=actor,
            finalized_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(record)
        try:
            await self.db.flush()
            sequence = 1
            if decision:
                self.db.add(
                    DecisionStep(
                        decision_id=record.id,
                        sequence=sequence,
                        component="rule",
                        status=(
                            "error"
                            if rule_errors
                            else ("matched" if rule_source else "fallback")
                        ),
                        input_json={"rule_type": rule_type},
                        output_json=sanitize_payload(
                            {
                                key: value
                                for key, value in decision.items()
                                if key != "_rule_trace"
                            }
                        ),
                        metadata_json={
                            "trace": sanitize_payload(rule_trace),
                            "trace_available": True,
                        },
                        error_code="rule_evaluation_incomplete"
                        if rule_errors
                        else None,
                    )
                )
                sequence += 1
            self.db.add(
                DecisionStep(
                    decision_id=record.id,
                    sequence=sequence,
                    component="rag",
                    status="matched" if kb_matches else "empty",
                    input_json={
                        "query": sanitize_payload(
                            f"{task.get('Name', '')} {task.get('Description', '')}".strip()
                        )
                    },
                    output_json={"matches": sanitize_payload(kb_matches)},
                    metadata_json={"passed_to_ai": bool(ai_source and kb_matches)},
                )
            )
            sequence += 1
            self.db.add(
                DecisionStep(
                    decision_id=record.id,
                    sequence=sequence,
                    component="ai",
                    status=(
                        "succeeded"
                        if ai_source
                        else ("fallback" if ai_text else "not_used")
                    ),
                    input_json={"history_limit": 5},
                    output_json={"text": sanitize_payload(ai_text)} if ai_text else {},
                    metadata_json=sanitize_payload(
                        ai_metadata or {"model": None, "backend": None}
                    ),
                    duration_ms=(ai_metadata or {}).get("duration_ms"),
                    input_tokens=(ai_metadata or {}).get("input_tokens"),
                    output_tokens=(ai_metadata or {}).get("output_tokens"),
                )
            )
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            existing = await self.db.scalar(
                select(DecisionRecord).where(
                    DecisionRecord.task_id == task_id,
                    DecisionRecord.context_fingerprint == fingerprint,
                    DecisionRecord.analysis_kind == "triage",
                )
            )
            if existing is None:
                raise
            return existing
        await self.db.refresh(record)
        return record

    async def record_triage_failure(
        self,
        *,
        task_id: int,
        task: dict[str, Any],
        history: list[dict[str, Any]] | None,
        error_code: str,
        actor: str,
        steps: list[dict[str, Any]] | None = None,
    ) -> DecisionRecord:
        history = history or []
        previous = await self.db.scalar(
            select(DecisionRecord)
            .where(DecisionRecord.task_id == task_id)
            .order_by(DecisionRecord.version.desc())
            .limit(1)
        )
        base_fingerprint = triage_context_fingerprint(task, history)
        fingerprint = hashlib.sha256(
            f"{base_fingerprint}:failed:{error_code}:{uuid.uuid4()}".encode("utf-8")
        ).hexdigest()
        now = dt.datetime.now(dt.timezone.utc)
        record = DecisionRecord(
            task_id=task_id,
            previous_decision_id=previous.id if previous else None,
            version=(previous.version if previous else 0) + 1,
            analysis_kind="triage",
            status="failed",
            outcome="no_solution",
            context_fingerprint=fingerprint,
            context_json=sanitize_payload(
                {
                    "task": task,
                    "ticket_fingerprint": ticket_snapshot_fingerprint(task, history),
                    "ticket_fingerprint_task": ticket_snapshot_fingerprint(task, []),
                    "analysis_revision": settings.ANALYSIS_REVISION,
                }
            ),
            envelope_json={
                "analysis_state": "system_error",
                "error_code": error_code,
                "gates": {
                    "can_send_response": False,
                    "can_execute_action": False,
                    "requires_approval": False,
                    "blocked_reasons": [error_code],
                },
            },
            created_by=actor,
            finalized_at=now,
        )
        self.db.add(record)
        await self.db.flush()
        for sequence, item in enumerate(steps or [], start=1):
            self.db.add(
                DecisionStep(
                    decision_id=record.id,
                    sequence=sequence,
                    component=str(item.get("component") or "pipeline"),
                    status=str(item.get("status") or "failed"),
                    input_json=sanitize_payload(item.get("input") or {}),
                    output_json=sanitize_payload(item.get("output") or {}),
                    metadata_json=sanitize_payload(item.get("metadata") or {}),
                    error_code=item.get("error_code") or error_code,
                    duration_ms=item.get("duration_ms"),
                )
            )
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def record_operational(
        self,
        *,
        task_id: int,
        ticket_run_id: uuid.UUID | None,
        action: str,
        target: dict[str, Any],
        parameters: dict[str, Any],
        actor: str,
        task: dict[str, Any] | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> DecisionRecord:
        history = history or []
        task_snapshot = task or {"Id": task_id}
        fingerprint_task = {
            **task_snapshot,
            "command_target": target,
            "command_parameters": parameters,
        }
        fingerprint = context_fingerprint(
            task=fingerprint_task,
            history=history,
            decision={"action": action},
            analysis_kind="execution",
        )
        existing = await self.db.scalar(
            select(DecisionRecord).where(
                DecisionRecord.task_id == task_id,
                DecisionRecord.context_fingerprint == fingerprint,
                DecisionRecord.analysis_kind == "execution",
            )
        )
        if existing:
            return existing
        previous = await self.db.scalar(
            select(DecisionRecord)
            .where(DecisionRecord.task_id == task_id)
            .order_by(DecisionRecord.version.desc())
            .limit(1)
        )
        record = DecisionRecord(
            task_id=task_id,
            ticket_run_id=ticket_run_id,
            previous_decision_id=previous.id if previous else None,
            version=(previous.version if previous else 0) + 1,
            analysis_kind="execution",
            status="finalized",
            outcome="proposal",
            context_fingerprint=fingerprint,
            envelope_json=sanitize_payload(
                {
                    "action": action,
                    "parameters": parameters,
                    "ready": True,
                    "policy": {},
                    "response": {"state": "valid", "text": "", "mode": "none"},
                    "gates": {
                        "can_execute_action": True,
                        "can_send_response": True,
                        "requires_approval": False,
                        "blocked_reasons": [],
                    },
                    "source": {"rule": False, "rag": False, "ai": False},
                    "completeness": {
                        "complete": True,
                        "missing_data": [],
                        "blocked_reasons": [],
                        "attachments_read": 0,
                    },
                }
            ),
            context_json=sanitize_payload(
                {
                    "task": task_snapshot,
                    "ticket_fingerprint": ticket_snapshot_fingerprint(task_snapshot, history),
                    "target": target,
                }
            ),
            created_by=actor,
            finalized_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(record)
        await self.db.flush()
        self.db.add(
            DecisionStep(
                decision_id=record.id,
                sequence=1,
                component="policy",
                status="proposed",
                input_json={"action": action},
                output_json={},
                metadata_json={},
            )
        )
        return record

    async def require_current(
        self, *, decision_id: uuid.UUID, task_id: int, version: int
    ) -> DecisionRecord:
        record = await self.db.get(DecisionRecord, decision_id)
        if record is None or record.task_id != task_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "decision_not_found_for_task")
        latest_version = await self.db.scalar(
            select(func.max(DecisionRecord.version)).where(
                DecisionRecord.task_id == task_id,
                DecisionRecord.analysis_kind == record.analysis_kind,
            )
        )
        if (
            record.version != version
            or record.version != latest_version
            or record.status != "finalized"
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "decision_stale")
        if (
            record.analysis_kind == "triage"
            and (record.context_json or {}).get("analysis_revision")
            != settings.ANALYSIS_REVISION
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "decision_stale")
        envelope = record.envelope_json or {}
        response = envelope.get("response") or {}
        gates = envelope.get("gates") or {}
        if response.get("state") not in {"valid", "fallback"}:
            raise HTTPException(status.HTTP_409_CONFLICT, "decision_response_invalid")
        if not (gates.get("can_send_response") or gates.get("can_execute_action")):
            raise HTTPException(status.HTTP_409_CONFLICT, "decision_blocked")
        return record

    async def latest_triage(self, task_id: int) -> DecisionRecord | None:
        return await self.db.scalar(
            select(DecisionRecord)
            .where(
                DecisionRecord.task_id == task_id,
                DecisionRecord.analysis_kind == "triage",
                DecisionRecord.status == "finalized",
            )
            .order_by(DecisionRecord.version.desc())
            .limit(1)
        )

    async def latest_triage_many(
        self, task_ids: list[int]
    ) -> dict[int, DecisionRecord]:
        if not task_ids:
            return {}
        records = list(
            (
                await self.db.scalars(
                    select(DecisionRecord)
                    .where(
                        DecisionRecord.task_id.in_(set(task_ids)),
                        DecisionRecord.analysis_kind == "triage",
                        DecisionRecord.status == "finalized",
                    )
                    .order_by(DecisionRecord.task_id, DecisionRecord.version.desc())
                )
            ).all()
        )
        latest: dict[int, DecisionRecord] = {}
        for record in records:
            latest.setdefault(record.task_id, record)
        return latest

    async def latest_triage_attempt(self, task_id: int) -> DecisionRecord | None:
        return await self.db.scalar(
            select(DecisionRecord)
            .where(
                DecisionRecord.task_id == task_id,
                DecisionRecord.analysis_kind == "triage",
            )
            .order_by(DecisionRecord.version.desc())
            .limit(1)
        )

    async def latest_triage_attempt_many(
        self, task_ids: list[int]
    ) -> dict[int, DecisionRecord]:
        if not task_ids:
            return {}
        records = list(
            (
                await self.db.scalars(
                    select(DecisionRecord)
                    .where(
                        DecisionRecord.task_id.in_(set(task_ids)),
                        DecisionRecord.analysis_kind == "triage",
                    )
                    .order_by(DecisionRecord.task_id, DecisionRecord.version.desc())
                )
            ).all()
        )
        latest: dict[int, DecisionRecord] = {}
        for record in records:
            latest.setdefault(record.task_id, record)
        return latest

    async def add_feedback(
        self,
        *,
        decision_id: uuid.UUID,
        verdict: str,
        reason_code: str | None = None,
        comment: str | None = None,
        final_action: dict[str, Any] | None = None,
        actor: str = "operator",
        commit: bool = True,
        attempt_id: uuid.UUID | None = None,
        event_id: uuid.UUID | str | None = None,
        source: str = "legacy",
        decision_version: int | None = None,
        task_id: int | None = None,
        response_variant_id: uuid.UUID | str | None = None,
        request_hash: str | None = None,
        operator_reason_code: str | None = None,
        reason_source: str | None = None,
    ) -> DecisionFeedback:
        decision = await self.db.get(DecisionRecord, decision_id)
        if decision is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "decision_not_found")

        allowed_verdicts = (
            VALID_FEEDBACK_VERDICTS | LEGACY_FEEDBACK_VERDICTS
            if source == "legacy"
            else VALID_FEEDBACK_VERDICTS
        )
        if verdict not in allowed_verdicts:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"invalid_verdict: {verdict}",
            )

        resolved_task_id = task_id or decision.task_id
        resolved_version = decision_version if decision_version is not None else decision.version

        # Приведение к UUID для корректных запросов и вставок
        decision_uuid = uuid.UUID(str(decision_id)) if decision_id else decision.id
        event_uuid = uuid.UUID(str(event_id)) if event_id else None
        attempt_uuid = uuid.UUID(str(attempt_id)) if attempt_id else None
        variant_uuid = uuid.UUID(str(response_variant_id)) if response_variant_id else None

        # Дедупликация по event_id для explicit feedback
        if event_uuid is not None:
            existing_by_event = await self.db.scalar(
                select(DecisionFeedback).where(
                    DecisionFeedback.event_id == event_uuid
                )
            )
            if existing_by_event is not None:
                same_decision = existing_by_event.decision_id == decision_uuid
                same_verdict = existing_by_event.verdict == verdict
                same_operator_reason = (
                    existing_by_event.operator_reason_code == operator_reason_code
                )
                if same_decision and same_verdict and same_operator_reason:
                    return existing_by_event
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "feedback_event_id_conflict",
                )

        # Дедупликация по attempt_id для apply feedback
        if attempt_uuid is not None:
            existing_by_attempt = await self.db.scalar(
                select(DecisionFeedback).where(
                    DecisionFeedback.attempt_id == attempt_uuid
                )
            )
            if existing_by_attempt is not None:
                same_decision = existing_by_attempt.decision_id == decision_uuid
                same_verdict = existing_by_attempt.verdict == verdict
                if same_decision and same_verdict:
                    return existing_by_attempt
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "feedback_attempt_conflict",
                )

        # Валидация словаря причин
        allowed_reasons = (
            VALID_FEEDBACK_REASONS | LEGACY_FEEDBACK_REASONS
            if source == "legacy"
            else VALID_FEEDBACK_REASONS
        )
        if operator_reason_code and operator_reason_code not in allowed_reasons:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"invalid_operator_reason_code: {operator_reason_code}",
            )
        if reason_code and reason_code not in allowed_reasons:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"invalid_reason_code: {reason_code}",
            )

        resolved_reason_code = reason_code
        resolved_reason_source = reason_source or ("legacy" if source == "legacy" else None)
        resolved_operator_reason = operator_reason_code

        if verdict == "accepted":
            resolved_reason_code = None
            resolved_reason_source = "none"
        elif operator_reason_code:
            resolved_reason_code = operator_reason_code
            resolved_reason_source = "operator"
        elif verdict in {"rejected", "commented"}:
            if not resolved_reason_code:
                resolved_reason_code = "other"
            resolved_reason_source = resolved_reason_source or "inferred"
        elif verdict == "modified":
            resolved_reason_source = resolved_reason_source or "inferred"

        feedback = DecisionFeedback(
            decision_id=decision_uuid,
            attempt_id=attempt_uuid,
            event_id=event_uuid,
            source=source,
            decision_version=resolved_version,
            task_id=resolved_task_id,
            response_variant_id=variant_uuid,
            request_hash=request_hash,
            verdict=verdict,
            reason_code=resolved_reason_code,
            reason_source=resolved_reason_source,
            operator_reason_code=resolved_operator_reason,
            comment=sanitize_payload(comment),
            final_action_json=sanitize_payload(final_action or {}),
            actor=actor,
        )
        self.db.add(feedback)
        if commit:
            await self.db.commit()
            await self.db.refresh(feedback)
        else:
            await self.db.flush()
        return feedback
