"""Transactional command state machine for the v2 execution boundary."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import (
    ActionPolicyRecord,
    CommandApproval,
    CommandAttempt,
    CommandEvent,
    CommandInbox,
    CommandOutbox,
    CommandRecord,
)
from app.services.actions.registry import PolicyMode, get_action_registry
from app.services.actions.policy import AUTO_ELIGIBLE_ACTIONS

TERMINAL_STATES = frozenset({"succeeded", "failed", "rejected", "cancelled", "needs_review"})
WINDOWS_STREAM = "stream:execution_commands:v2"
BACKEND_STREAM = "stream:backend_commands:v2"


def canonical_hash(action: str, target: dict[str, Any], parameters: dict[str, Any]) -> str:
    raw = json.dumps(
        {"action": action, "target": target, "parameters": parameters},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _required_fields(action_def, target: dict[str, Any], parameters: dict[str, Any]) -> list[str]:
    values = {**target, **parameters}
    required = action_def.parameters_schema.get("required", [])
    return [name for name in required if values.get(name) in (None, "")]


def serialize_command(command: CommandRecord) -> dict[str, Any]:
    return {
        "command_id": str(command.id),
        "idempotency_key": command.idempotency_key,
        "action": command.action,
        "executor": command.executor,
        "target": command.target_json,
        "parameters": command.params_json,
        "status": command.status,
        "version": command.version,
        "priority": command.priority,
        "initiator": command.initiator,
        "source": command.source,
        "task_id": command.task_id,
        "result": command.result_json,
        "error_message": command.error_message,
        "created_at": command.created_at.isoformat() if command.created_at else None,
        "updated_at": command.updated_at.isoformat() if command.updated_at else None,
        "completed_at": command.completed_at.isoformat() if command.completed_at else None,
    }


@dataclass(slots=True)
class Claim:
    command: CommandRecord
    token: str
    attempt_no: int


class CommandService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.registry = get_action_registry()

    async def _policy_mode(self, action: str) -> PolicyMode:
        override = await self.db.get(ActionPolicyRecord, action)
        if override:
            try:
                mode = PolicyMode(override.mode)
            except ValueError:
                return PolicyMode.DISABLED
            if mode == PolicyMode.AUTO and action not in AUTO_ELIGIBLE_ACTIONS:
                return PolicyMode.CONFIRM
            return mode
        action_def = self.registry.get(action)
        return action_def.default_mode if action_def else PolicyMode.DISABLED

    async def create(
        self,
        *,
        action: str,
        target: dict[str, Any],
        parameters: dict[str, Any],
        idempotency_key: str,
        initiator: str,
        source: str,
        priority: int,
    ) -> tuple[CommandRecord, bool]:
        action_def = self.registry.get(action)
        if action_def is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown action: {action}")
        if not action_def.implemented:
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED,
                f"Action '{action}' has no production executor",
            )
        missing = _required_fields(action_def, target, parameters)
        if missing:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"missing_fields": missing})

        request_hash = canonical_hash(action, target, parameters)
        existing = await self.db.scalar(
            select(CommandRecord).where(CommandRecord.idempotency_key == idempotency_key)
        )
        if existing:
            if existing.request_hash != request_hash:
                raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency-Key was used for another request")
            return existing, True

        mode = await self._policy_mode(action)
        if mode == PolicyMode.DISABLED:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Action '{action}' is disabled")
        command_status = "queued" if mode == PolicyMode.AUTO else "awaiting_approval"
        task_id_raw = target.get("task_id") or parameters.get("task_id")
        try:
            task_id = int(task_id_raw) if task_id_raw is not None else None
        except (TypeError, ValueError):
            task_id = None

        command = CommandRecord(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            action=action,
            executor=action_def.executor,
            target_json=target,
            params_json=parameters,
            status=command_status,
            priority=priority,
            initiator=initiator,
            source=source,
            task_id=task_id,
        )
        self.db.add(command)
        try:
            await self.db.flush()
            self.db.add(CommandEvent(
                command_id=command.id,
                sequence=1,
                event_type="created",
                details_json={"status": command_status},
                actor=initiator,
            ))
            if command_status == "queued":
                self._enqueue(command)
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            existing = await self.db.scalar(
                select(CommandRecord).where(CommandRecord.idempotency_key == idempotency_key)
            )
            if existing and existing.request_hash == request_hash:
                return existing, True
            raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency-Key conflict")
        await self.db.refresh(command)
        return command, False

    def _enqueue(self, command: CommandRecord, *, delay_seconds: int = 0) -> None:
        stream = WINDOWS_STREAM if command.executor == "windows" else BACKEND_STREAM
        self.db.add(CommandOutbox(
            command_id=command.id,
            stream=stream,
            payload_json={
                "command_id": str(command.id),
                "action": command.action,
                "executor": command.executor,
                "version": command.version,
            },
            available_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay_seconds),
        ))

    async def get(self, command_id: uuid.UUID, *, for_update: bool = False) -> CommandRecord:
        stmt = select(CommandRecord).where(CommandRecord.id == command_id)
        if for_update:
            stmt = stmt.with_for_update()
        command = await self.db.scalar(stmt)
        if command is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Command not found")
        return command

    async def approve(
        self, command_id: uuid.UUID, *, decision: str, reason: str | None, operator: str
    ) -> CommandRecord:
        command = await self.get(command_id, for_update=True)
        if command.status != "awaiting_approval":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Command is {command.status}")
        if decision not in {"approve", "reject"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid decision")
        if decision == "reject" and not (reason or "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Reason is required for rejection")

        self.db.add(CommandApproval(
            command_id=command.id,
            decision=decision,
            reason=reason,
            operator=operator,
            request_hash=command.request_hash,
        ))
        command.version += 1
        command.status = "queued" if decision == "approve" else "rejected"
        if decision == "reject":
            command.completed_at = dt.datetime.now(dt.timezone.utc)
        sequence = await self._next_sequence(command.id)
        self.db.add(CommandEvent(
            command_id=command.id,
            sequence=sequence,
            event_type="approved" if decision == "approve" else "rejected",
            details_json={"reason": reason},
            actor=operator,
        ))
        if decision == "approve":
            self._enqueue(command)
        await self.db.commit()
        await self.db.refresh(command)
        return command

    async def claim(
        self,
        command_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        message_id: str | None = None,
        outbox_id: uuid.UUID | None = None,
    ) -> Claim:
        command = await self.get(command_id, for_update=True)
        if message_id:
            duplicate_message = await self.db.scalar(
                select(CommandInbox).where(
                    CommandInbox.consumer == worker_id,
                    CommandInbox.message_id == message_id,
                )
            )
            if duplicate_message:
                raise HTTPException(status.HTTP_409_CONFLICT, "Transport message already claimed")
        if command.status in TERMINAL_STATES:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Command is {command.status}")
        if command.status != "queued":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Command is {command.status}")
        token = secrets.token_urlsafe(32)
        now = dt.datetime.now(dt.timezone.utc)
        attempt_no = int(await self.db.scalar(
            select(func.count(CommandAttempt.id)).where(CommandAttempt.command_id == command.id)
        ) or 0) + 1
        command.status = "running"
        command.version += 1
        command.lease_token_hash = hashlib.sha256(token.encode()).hexdigest()
        command.lease_expires_at = now + dt.timedelta(seconds=lease_seconds)
        self.db.add(CommandAttempt(
            command_id=command.id,
            attempt_no=attempt_no,
            worker_id=worker_id,
            status="running",
        ))
        if message_id:
            self.db.add(CommandInbox(
                command_id=command.id,
                consumer=worker_id,
                message_id=message_id,
                outbox_id=outbox_id,
            ))
        self.db.add(CommandEvent(
            command_id=command.id,
            sequence=await self._next_sequence(command.id),
            event_type="started",
            details_json={"worker_id": worker_id, "attempt_no": attempt_no},
            actor=worker_id,
        ))
        await self.db.commit()
        await self.db.refresh(command)
        return Claim(command=command, token=token, attempt_no=attempt_no)

    async def finish(
        self,
        command_id: uuid.UUID,
        *,
        claim_token: str,
        outcome: str,
        result: dict[str, Any],
        error_message: str | None,
        worker_id: str,
    ) -> CommandRecord:
        if outcome not in {"succeeded", "failed", "needs_review"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid outcome")
        command = await self.get(command_id, for_update=True)
        token_hash = hashlib.sha256(claim_token.encode()).hexdigest()
        if command.status != "running" or not secrets.compare_digest(command.lease_token_hash or "", token_hash):
            raise HTTPException(status.HTTP_409_CONFLICT, "Stale or invalid claim")
        attempt = await self.db.scalar(
            select(CommandAttempt)
            .where(CommandAttempt.command_id == command.id, CommandAttempt.status == "running")
            .order_by(CommandAttempt.attempt_no.desc())
        )
        retry_delay: int | None = None
        if outcome == "failed" and command.action in AUTO_ELIGIBLE_ACTIONS and attempt:
            retry_delay = {1: 5, 2: 30}.get(attempt.attempt_no)

        command.status = "queued" if retry_delay is not None else outcome
        command.version += 1
        command.result_json = result
        command.error_message = error_message
        finished_at = dt.datetime.now(dt.timezone.utc)
        command.completed_at = None if retry_delay is not None else finished_at
        command.lease_token_hash = None
        command.lease_expires_at = None
        if attempt:
            attempt.status = outcome
            attempt.result_json = result
            attempt.error_message = error_message
            attempt.completed_at = finished_at
        if retry_delay is not None:
            self._enqueue(command, delay_seconds=retry_delay)
        self.db.add(CommandEvent(
            command_id=command.id,
            sequence=await self._next_sequence(command.id),
            event_type="retry_scheduled" if retry_delay is not None else outcome,
            details_json={
                "worker_id": worker_id,
                "error": error_message,
                "retry_in_seconds": retry_delay,
            },
            actor=worker_id,
        ))
        await self.db.commit()
        await self.db.refresh(command)
        return command

    async def cancel(self, command_id: uuid.UUID, *, reason: str, actor: str) -> CommandRecord:
        command = await self.get(command_id, for_update=True)
        if command.status not in {"awaiting_approval", "queued"}:
            raise HTTPException(status.HTTP_409_CONFLICT, "Command can no longer be cancelled safely")
        command.status = "cancelled"
        command.version += 1
        command.error_message = reason
        command.completed_at = dt.datetime.now(dt.timezone.utc)
        self.db.add(CommandEvent(
            command_id=command.id,
            sequence=await self._next_sequence(command.id),
            event_type="cancelled",
            details_json={"reason": reason},
            actor=actor,
        ))
        await self.db.commit()
        await self.db.refresh(command)
        return command

    async def resolve_review(
        self,
        command_id: uuid.UUID,
        *,
        decision: str,
        reason: str,
        actor: str,
    ) -> CommandRecord:
        command = await self.get(command_id, for_update=True)
        if command.status != "needs_review":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Command is {command.status}")
        if decision not in {"succeeded", "failed", "requeue"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid review decision")
        command.version += 1
        command.error_message = reason
        command.completed_at = None
        if decision == "requeue":
            command.status = "queued"
            self._enqueue(command)
        else:
            command.status = decision
            command.completed_at = dt.datetime.now(dt.timezone.utc)
        self.db.add(CommandEvent(
            command_id=command.id,
            sequence=await self._next_sequence(command.id),
            event_type="review_resolved",
            details_json={"decision": decision, "reason": reason},
            actor=actor,
        ))
        await self.db.commit()
        await self.db.refresh(command)
        return command

    async def set_policy(self, action: str, *, mode: str, actor: str) -> ActionPolicyRecord:
        action_def = self.registry.get(action)
        if action_def is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown action")
        try:
            policy_mode = PolicyMode(mode)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid policy mode") from exc
        if policy_mode == PolicyMode.AUTO and action not in AUTO_ELIGIBLE_ACTIONS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Only statically safe actions can run automatically",
            )
        record = await self.db.get(ActionPolicyRecord, action)
        if record is None:
            record = ActionPolicyRecord(action=action, mode=policy_mode.value, updated_by=actor)
            self.db.add(record)
        else:
            record.mode = policy_mode.value
            record.updated_by = actor
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def _next_sequence(self, command_id: uuid.UUID) -> int:
        current = await self.db.scalar(
            select(func.max(CommandEvent.sequence)).where(CommandEvent.command_id == command_id)
        )
        return int(current or 0) + 1
