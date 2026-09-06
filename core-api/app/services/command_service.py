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
    SecurityEvent,
    AutopilotSetting,
    TicketRun,
    TicketRunEvent,
)
from app.services.actions.registry import PolicyMode, get_action_registry
from app.services.actions.policy import AUTO_ELIGIBLE_ACTIONS, AUTO_RETRY_ELIGIBLE_ACTIONS
from app.config import settings

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
        "initiator_principal_id": (
            str(command.initiator_principal_id) if command.initiator_principal_id else None
        ),
        "source": command.source,
        "task_id": command.task_id,
        "ticket_run_id": str(command.ticket_run_id) if command.ticket_run_id else None,
        "decision_id": str(command.decision_id) if command.decision_id else None,
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

    async def _assert_ticket_run_allows_execution(
        self, ticket_run_id: uuid.UUID | None, *, task_id: int | None = None
    ) -> TicketRun | None:
        if ticket_run_id is None:
            if task_id is not None:
                active_run_id = await self.db.scalar(
                    select(TicketRun.id).where(
                        TicketRun.task_id == task_id,
                        TicketRun.completed_at.is_(None),
                    )
                )
                if active_run_id is not None:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "Active ticket run must be linked to the command",
                    )
            return None
        run = await self.db.get(TicketRun, ticket_run_id)
        if run is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ticket run not found")
        if run.completed_at is not None or run.state != "running":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Ticket run is {run.state}")
        if task_id is not None and run.task_id != task_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Command task does not match ticket run")
        if run.mode == "autopilot":
            setting = await self.db.get(AutopilotSetting, "global")
            if setting is None or not setting.enabled:
                raise HTTPException(status.HTTP_409_CONFLICT, "Autopilot is disabled")
        running_command = await self.db.scalar(
            select(CommandRecord.id)
            .where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.status == "running",
            )
            .limit(1)
        )
        if running_command is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Ticket run already has an operation in progress",
            )
        return run

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
        initiator_principal_id: uuid.UUID | None = None,
        ticket_run_id: uuid.UUID | None = None,
        decision_id: uuid.UUID | None = None,
        decision_version: int | None = None,
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
        await self._assert_ticket_run_allows_execution(ticket_run_id, task_id=task_id)
        if decision_id is None and source == "web" and task_id is not None:
            from app.services.decision_journal import DecisionJournalService

            generated_decision = await DecisionJournalService(self.db).record_operational(
                task_id=task_id,
                ticket_run_id=ticket_run_id,
                action=action,
                target=target,
                parameters=parameters,
                actor=initiator,
            )
            decision_id = generated_decision.id
            decision_version = generated_decision.version
        if decision_id is not None:
            if task_id is None or decision_version is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "decision_version_and_task_id_required",
                )
            from app.services.decision_journal import DecisionJournalService

            await DecisionJournalService(self.db).require_current(
                decision_id=decision_id,
                task_id=task_id,
                version=decision_version,
            )
        elif source in {"autopilot", "triage", "assistant"}:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "decision_id_required",
            )

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
            initiator_principal_id=initiator_principal_id,
            source=source,
            task_id=task_id,
            ticket_run_id=ticket_run_id,
            decision_id=decision_id,
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
        self,
        command_id: uuid.UUID,
        *,
        decision: str,
        reason: str | None,
        operator: str,
        approver_principal_id: uuid.UUID | None = None,
        approver_roles: frozenset[str] = frozenset(),
        approver_permissions: frozenset[str] | None = None,
    ) -> CommandRecord:
        command = await self.get(command_id, for_update=True)
        if command.status != "awaiting_approval":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Command is {command.status}")
        if decision not in {"approve", "reject"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid decision")
        if decision == "reject" and not (reason or "").strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Reason is required for rejection")

        action_def = self.registry.get(command.action)
        risk_level = action_def.risk_level if action_def else 3
        required_permission = f"command:approve:r{max(1, risk_level)}"
        if approver_permissions is not None and (
            required_permission not in approver_permissions and "*" not in approver_permissions
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {required_permission}")
        if (
            risk_level >= 2
            and approver_principal_id is not None
            and command.initiator_principal_id == approver_principal_id
            and "system_admin" not in approver_roles
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "R2 command must be approved by another operator or a system administrator",
            )

        self.db.add(CommandApproval(
            command_id=command.id,
            decision=decision,
            reason=reason,
            operator=operator,
            approver_principal_id=approver_principal_id,
            request_hash=command.request_hash,
        ))
        self.db.add(SecurityEvent(
            event_type="command.approval",
            outcome=decision,
            principal_id=approver_principal_id,
            auth_method="command_api",
            resource_type="command",
            resource_id=str(command.id),
            details_json={"action": command.action, "risk_level": risk_level},
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
        if command.ticket_run_id:
            run = await self.db.get(TicketRun, command.ticket_run_id)
            if run is not None and run.completed_at is None:
                run.version += 1
                run.updated_by = operator
                if decision == "approve":
                    run.state = "running"
                    run.pause_reason = None
                    run_event_type = "command_approved"
                else:
                    run.state = "paused"
                    run.pause_reason = "approval_rejected"
                    run_event_type = "command_rejected"
                run_sequence = int(
                    await self.db.scalar(
                        select(func.max(TicketRunEvent.sequence)).where(
                            TicketRunEvent.ticket_run_id == run.id
                        )
                    )
                    or 0
                ) + 1
                self.db.add(
                    TicketRunEvent(
                        ticket_run_id=run.id,
                        sequence=run_sequence,
                        event_type=run_event_type,
                        actor=operator,
                        details_json={"command_id": str(command.id)},
                    )
                )
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
        now = dt.datetime.now(dt.timezone.utc)
        if command.status == "running":
            lease_expires_at = command.lease_expires_at
            if lease_expires_at is not None and lease_expires_at.tzinfo is None:
                lease_expires_at = lease_expires_at.replace(tzinfo=dt.timezone.utc)
            if lease_expires_at is not None and lease_expires_at <= now:
                attempt = await self.db.scalar(
                    select(CommandAttempt)
                    .where(
                        CommandAttempt.command_id == command.id,
                        CommandAttempt.status == "running",
                    )
                    .order_by(CommandAttempt.attempt_no.desc())
                )
                if attempt is not None:
                    attempt.status = "needs_review"
                    attempt.completed_at = now
                    attempt.error_message = "claim_lease_expired_result_unknown"
                command.status = "needs_review"
                command.error_message = "claim_lease_expired_result_unknown"
                command.lease_token_hash = None
                command.lease_expires_at = None
                command.completed_at = now
                command.version += 1
                self.db.add(
                    CommandEvent(
                        command_id=command.id,
                        sequence=await self._next_sequence(command.id),
                        event_type="lease_expired_needs_review",
                        details_json={"reason": "external_result_unknown"},
                        actor=worker_id,
                    )
                )
                await self.db.commit()
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {
                        "command_status": "needs_review",
                        "reason": "claim_lease_expired_result_unknown",
                    },
                )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"command_status": "running", "reason": "claim_lease_active"},
            )
        if command.status in TERMINAL_STATES:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"command_status": command.status, "reason": "command_terminal"},
            )
        if message_id:
            duplicate_message = await self.db.scalar(
                select(CommandInbox).where(
                    CommandInbox.consumer == worker_id,
                    CommandInbox.message_id == message_id,
                )
            )
            if duplicate_message:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {"command_status": command.status, "reason": "transport_message_claimed"},
                )
        if command.status != "queued":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"command_status": command.status, "reason": "command_not_queued"},
            )
        if await self._policy_mode(command.action) == PolicyMode.DISABLED:
            command.status = "cancelled"
            command.version += 1
            command.error_message = "action_policy_disabled_before_execution"
            command.completed_at = dt.datetime.now(dt.timezone.utc)
            self.db.add(
                CommandEvent(
                    command_id=command.id,
                    sequence=await self._next_sequence(command.id),
                    event_type="policy_blocked",
                    details_json={"reason": command.error_message},
                    actor=worker_id,
                )
            )
            await self.db.commit()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Command action was disabled before execution",
            )
        await self._assert_ticket_run_allows_execution(
            command.ticket_run_id, task_id=command.task_id
        )
        token = secrets.token_urlsafe(32)
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
        if outcome == "failed" and command.action in AUTO_RETRY_ELIGIBLE_ACTIONS and attempt:
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
        if (
            retry_delay is None
            and outcome == "succeeded"
            and command.action == "apply_triage"
            and command.ticket_run_id is not None
        ):
            run = await self.db.scalar(
                select(TicketRun)
                .where(TicketRun.id == command.ticket_run_id)
                .with_for_update()
            )
            final_status_id = command.params_json.get("status_id")
            terminal_status_ids = {
                settings.STATUS_COMPLETED_ID,
                settings.STATUS_CANCELLED_ID,
                settings.STATUS_CLOSED_ID,
            }
            if (
                run is not None
                and run.mode == "manual"
                and run.completed_at is None
                and final_status_id in terminal_status_ids
            ):
                run.state = "completed"
                run.outcome = (
                    "cancelled"
                    if final_status_id == settings.STATUS_CANCELLED_ID
                    else "completed"
                )
                run.completed_at = finished_at
                run.version += 1
                run.updated_by = command.initiator
                run_sequence = (
                    await self.db.scalar(
                        select(func.max(TicketRunEvent.sequence)).where(
                            TicketRunEvent.ticket_run_id == run.id
                        )
                    )
                    or 0
                ) + 1
                self.db.add(
                    TicketRunEvent(
                        ticket_run_id=run.id,
                        sequence=run_sequence,
                        event_type="run_completed",
                        details_json={
                            "outcome": run.outcome,
                            "status_id": final_status_id,
                            "command_id": str(command.id),
                        },
                        actor=command.initiator,
                    )
                )
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

    async def set_policy(
        self,
        action: str,
        *,
        mode: str,
        actor: str,
        reason: str | None = None,
        principal_id: uuid.UUID | None = None,
    ) -> ActionPolicyRecord:
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
        self.db.add(SecurityEvent(
            event_type="policy.changed",
            outcome="success",
            principal_id=principal_id,
            auth_method="command_api",
            resource_type="action_policy",
            resource_id=action,
            details_json={"mode": policy_mode.value, "reason": reason},
        ))
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def _next_sequence(self, command_id: uuid.UUID) -> int:
        current = await self.db.scalar(
            select(func.max(CommandEvent.sequence)).where(CommandEvent.command_id == command_id)
        )
        return int(current or 0) + 1
