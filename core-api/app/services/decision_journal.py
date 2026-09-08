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

from app.database.db import DecisionFeedback, DecisionRecord, DecisionStep
from app.services.ai_suggestions import action_for_decision, missing_data_for_decision


SECRET_KEY_RE = re.compile(r"(password|passwd|secret|token|authorization|credential)", re.I)
SECRET_TEXT_RE = re.compile(
    r"(?i)\b(password|пароль|token|токен|secret)\s*[:=]\s*([^\s,;]+)"
)
MAX_TEXT_LENGTH = 8_000


def sanitize_payload(value: Any) -> Any:
    """Remove obvious secrets and bound text copied from an external ticket."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else sanitize_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value[:200]]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value[:200]]
    if isinstance(value, str):
        cleaned = SECRET_TEXT_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", value)
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
    encoded = json.dumps(sanitize_payload(material), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def ticket_snapshot_fingerprint(
    task: dict[str, Any], history: list[dict[str, Any]] | None = None
) -> str:
    """Fingerprint only mutable external ticket data, independent of the proposal."""
    task_keys = (
        "Id", "StatusId", "ServiceId", "ExecutorId", "ExecutorIds", "Name",
        "Description", "Changed", "CustomFields", "Attachments", "attachments",
    )
    history_keys = (
        "Id", "id", "Comments", "Comment", "Text", "EditorId", "Editor",
        "Created", "Date", "Changed",
    )
    material = {
        "task": {key: task.get(key) for key in task_keys if key in task},
        "history": [
            {key: item.get(key) for key in history_keys if key in item}
            for item in (history or [])
            if isinstance(item, dict)
        ],
    }
    encoded = json.dumps(sanitize_payload(material), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _history_context(history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Any]]:
    meaningful = [
        item
        for item in history
        if str(item.get("Comments") or item.get("Comment") or item.get("Text") or "").strip()
    ]
    used = meaningful[-5:]
    omitted = [item.get("Id") or item.get("id") for item in meaningful[:-5]]
    return used, omitted


def serialize_decision(record: DecisionRecord, *, steps: list[DecisionStep] | None = None) -> dict[str, Any]:
    payload = {
        "id": str(record.id),
        "task_id": record.task_id,
        "ticket_run_id": str(record.ticket_run_id) if record.ticket_run_id else None,
        "previous_decision_id": str(record.previous_decision_id) if record.previous_decision_id else None,
        "version": record.version,
        "analysis_kind": record.analysis_kind,
        "status": record.status,
        "outcome": record.outcome,
        "fingerprint": record.context_fingerprint,
        "sources": record.source_json,
        "context": record.context_json,
        "completeness": record.completeness_json,
        "proposal": record.proposal_json,
        "policy": record.policy_json,
        "created_by": record.created_by,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "finalized_at": record.finalized_at.isoformat() if record.finalized_at else None,
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


class DecisionJournalService:
    def __init__(self, db: AsyncSession):
        self.db = db

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
        actor: str = "system:triage",
        force: bool = False,
    ) -> DecisionRecord:
        history = history or []
        kb_matches = kb_matches or []
        fingerprint = context_fingerprint(
            task=task, history=history, decision=decision, analysis_kind="triage"
        )
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
        outcome = (
            "proposal" if decision else "no_solution"
        )
        record = DecisionRecord(
            task_id=task_id,
            ticket_run_id=ticket_run_id,
            previous_decision_id=previous.id if previous else None,
            version=version,
            analysis_kind="triage",
            status="finalized",
            outcome=outcome,
            context_fingerprint=fingerprint,
            source_json={
                "rule": rule_source,
                "rag": rag_source,
                "ai": ai_source,
                "typed_outcome_schema": (typed_outcome or {}).get("schema_version"),
                "rule_key": (typed_outcome or {}).get("rule_key"),
                "rule_version": (typed_outcome or {}).get("rule_version"),
            },
            context_json=sanitize_payload(
                {
                    "task": task,
                    "ticket_fingerprint": ticket_snapshot_fingerprint(task, history),
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
            completeness_json={
                "complete": not missing_data and not rule_errors,
                "missing_data": missing_data,
                "blocked_reasons": blocked_reasons,
                "limitations": limitations,
                "history_total": len(history),
                "history_used": len(used_history),
                "attachments_total": len(attachments),
                "attachments_read": 0,
            },
            proposal_json=sanitize_payload(
                {
                    "action": action,
                    "title": (decision or {}).get("name"),
                    "comment": ai_text or (decision or {}).get("comment"),
                    "status_id": (decision or {}).get("status_id"),
                    "status_name": (decision or {}).get("status_name"),
                    "expenses": (decision or {}).get("expenses"),
                    "consequences": "Изменит заявку в IntraService" if decision else None,
                    "ready": bool(decision) and not missing_data and not rule_errors,
                    "trigger_markers": (decision or {}).get("trigger_markers", []),
                    "risk_level": (decision or {}).get("risk_level", "normal"),
                    "risk_warning": (decision or {}).get("risk_warning"),
                    "typed_outcome": typed_outcome,
                    "decision_envelope": (decision or {}).get("_decision_envelope"),
                    "action_parameters": (decision or {}).get("action_parameters"),
                }
            ),
            policy_json=sanitize_payload(policy or {}),
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
                            "error" if rule_errors else ("matched" if rule_source else "fallback")
                        ),
                        input_json={"rule_type": rule_type},
                        output_json=sanitize_payload(
                            {key: value for key, value in decision.items() if key != "_rule_trace"}
                        ),
                        metadata_json={"trace": sanitize_payload(rule_trace), "trace_available": True},
                        error_code="rule_evaluation_incomplete" if rule_errors else None,
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
                    metadata_json=sanitize_payload(ai_metadata or {"model": None, "backend": None}),
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
            source_json={"rule": False, "rag": False, "ai": False},
            context_json=sanitize_payload(
                {
                    "task": task_snapshot,
                    "ticket_fingerprint": (
                        ticket_snapshot_fingerprint(task_snapshot, history)
                        if task is not None
                        else None
                    ),
                    "target": target,
                }
            ),
            completeness_json={"complete": True, "missing_data": [], "blocked_reasons": []},
            proposal_json=sanitize_payload(
                {"action": action, "parameters": parameters, "ready": True}
            ),
            policy_json={},
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
            select(func.max(DecisionRecord.version)).where(DecisionRecord.task_id == task_id)
        )
        if record.version != version or record.version != latest_version or record.status != "finalized":
            raise HTTPException(status.HTTP_409_CONFLICT, "decision_stale")
        if record.completeness_json.get("complete") is False:
            raise HTTPException(status.HTTP_409_CONFLICT, "decision_incomplete")
        return record

    async def add_feedback(
        self,
        *,
        decision_id: uuid.UUID,
        verdict: str,
        reason_code: str | None,
        comment: str | None,
        final_action: dict[str, Any],
        actor: str,
    ) -> DecisionFeedback:
        if await self.db.get(DecisionRecord, decision_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "decision_not_found")
        feedback = DecisionFeedback(
            decision_id=decision_id,
            verdict=verdict,
            reason_code=reason_code,
            comment=sanitize_payload(comment),
            final_action_json=sanitize_payload(final_action),
            actor=actor,
        )
        self.db.add(feedback)
        await self.db.commit()
        await self.db.refresh(feedback)
        return feedback
