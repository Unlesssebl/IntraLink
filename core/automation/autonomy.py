"""Durable assignment-triggered autonomy primitives.

Assignment is the admission mandate.  Everything in this module after a run is
created is a technical execution invariant, never an operator policy gate.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.contracts import ActionPlan, ActionPlanState, compute_action_plan_hash
from core.automation.persistence import canonical_params_hash
from core.database.models import (
    ActionPlanRecord,
    ActionPreflightRecord,
    AutonomyCommentOutboxRecord,
    AutonomyRunEventRecord,
    AutonomyRunRecord,
    AutonomyTicketStateRecord,
    ExecutionPermitRecord,
    sanitize_secret_text,
)
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO

ACTIVE_RUN_STATES = {
    "detected",
    "dispatching",
    "analyzing",
    "awaiting_facts",
    "retry_wait",
    "ready",
    "executing",
}
TERMINAL_RUN_STATES = {"succeeded", "blocked", "cancelled", "unknown_outcome"}
RETRY_DELAYS = (30, 120, 600)


@dataclass(frozen=True)
class AssignmentObservation:
    run: AutonomyRunRecord | None
    created: bool = False
    cancelled: bool = False
    baseline: bool = False


class AutonomyRunCoordinator:
    """Projects bot assignment edges into exactly one durable run per epoch."""

    async def observe(
        self,
        session: AsyncSession,
        *,
        task: TaskDTO,
        bot_user_id: int,
        is_final: bool,
        lifetime: list[TaskLifetimeEventDTO] | None = None,
        bot_display_name: str = "",
        baseline: bool = False,
    ) -> AssignmentObservation:
        assigned = bot_user_id in task.get_executor_ids()
        record = await session.scalar(
            select(AutonomyTicketStateRecord)
            .where(AutonomyTicketStateRecord.task_id == task.id)
            .with_for_update()
        )
        latest_event_key = self._latest_event_key(lifetime or [])
        assignment_event_key = self._assignment_event_key(
            task.id,
            bot_user_id,
            lifetime or [],
            bot_display_name,
            latest_event_key,
        )

        if record is None:
            candidate = AutonomyTicketStateRecord(
                task_id=task.id,
                bot_user_id=bot_user_id,
                bot_assigned=assigned,
                assignment_epoch=1 if assigned and not baseline and not is_final else 0,
                last_assignment_event_key=assignment_event_key if assigned else None,
                last_lifetime_event_key=latest_event_key,
                status_id=task.status_id,
                is_final=is_final,
            )
            inserted = False
            try:
                async with session.begin_nested():
                    session.add(candidate)
                    await session.flush()
                record = candidate
                inserted = True
            except IntegrityError:
                record = await session.scalar(
                    select(AutonomyTicketStateRecord)
                    .where(AutonomyTicketStateRecord.task_id == task.id)
                    .with_for_update()
                )
                if record is None:
                    raise
            if inserted:
                if not assigned or baseline or is_final:
                    return AssignmentObservation(run=None, baseline=baseline and assigned)
                run, created = await self._create_run(
                    session,
                    task_id=task.id,
                    bot_user_id=bot_user_id,
                    epoch=record.assignment_epoch,
                    assignment_event_key=assignment_event_key,
                    cancelled=is_final,
                    reason="ticket_final_at_detection" if is_final else None,
                )
                return AssignmentObservation(run=run, created=created, cancelled=is_final)

        previously_assigned = record.bot_assigned
        previous_lifetime_event_key = record.last_lifetime_event_key
        record.bot_user_id = bot_user_id
        record.bot_assigned = assigned
        record.status_id = task.status_id
        record.is_final = is_final
        record.last_lifetime_event_key = latest_event_key or record.last_lifetime_event_key

        if (
            not previously_assigned
            and not assigned
            and self._assignment_seen_since(
                lifetime or [], previous_lifetime_event_key, bot_display_name
            )
        ):
            record.assignment_epoch += 1
            record.last_assignment_event_key = assignment_event_key
            run, created = await self._create_run(
                session,
                task_id=task.id,
                bot_user_id=bot_user_id,
                epoch=record.assignment_epoch,
                assignment_event_key=assignment_event_key,
                cancelled=True,
                reason="bot_unassigned_before_poll",
            )
            return AssignmentObservation(run=run, created=created, cancelled=True)

        if assigned and not previously_assigned:
            if is_final:
                record.last_assignment_event_key = assignment_event_key
                return AssignmentObservation(run=None)
            record.assignment_epoch += 1
            record.last_assignment_event_key = assignment_event_key
            run, created = await self._create_run(
                session,
                task_id=task.id,
                bot_user_id=bot_user_id,
                epoch=record.assignment_epoch,
                assignment_event_key=assignment_event_key,
                cancelled=is_final,
                reason="ticket_final_at_detection" if is_final else None,
            )
            return AssignmentObservation(run=run, created=created, cancelled=is_final)

        active = await self._active_run(session, task.id, bot_user_id)
        if active is not None and (not assigned or is_final):
            reason = "bot_unassigned" if not assigned else "ticket_became_final"
            active.state = "cancelled"
            active.blocker_code = reason
            active.completed_at = datetime.now(UTC)
            self.add_event(session, active, "cancelled", {"reason": reason})
            return AssignmentObservation(run=active, cancelled=True)
        return AssignmentObservation(run=active)

    @staticmethod
    async def _active_run(
        session: AsyncSession, task_id: int, bot_user_id: int
    ) -> AutonomyRunRecord | None:
        return await session.scalar(
            select(AutonomyRunRecord)
            .where(
                AutonomyRunRecord.task_id == task_id,
                AutonomyRunRecord.bot_user_id == bot_user_id,
                AutonomyRunRecord.state.in_(ACTIVE_RUN_STATES),
            )
            .order_by(AutonomyRunRecord.assignment_epoch.desc())
            .limit(1)
        )

    async def _create_run(
        self,
        session: AsyncSession,
        *,
        task_id: int,
        bot_user_id: int,
        epoch: int,
        assignment_event_key: str,
        cancelled: bool,
        reason: str | None,
    ) -> tuple[AutonomyRunRecord, bool]:
        existing = await session.scalar(
            select(AutonomyRunRecord).where(
                AutonomyRunRecord.task_id == task_id,
                AutonomyRunRecord.bot_user_id == bot_user_id,
                AutonomyRunRecord.assignment_epoch == epoch,
            )
        )
        if existing is not None:
            return existing, False
        state = "cancelled" if cancelled else "detected"
        run = AutonomyRunRecord(
            task_id=task_id,
            bot_user_id=bot_user_id,
            assignment_epoch=epoch,
            assignment_event_key=assignment_event_key,
            state=state,
            blocker_code=reason,
            completed_at=datetime.now(UTC) if cancelled else None,
        )
        try:
            async with session.begin_nested():
                session.add(run)
                await session.flush()
        except IntegrityError:
            run = await session.scalar(
                select(AutonomyRunRecord).where(
                    AutonomyRunRecord.task_id == task_id,
                    AutonomyRunRecord.bot_user_id == bot_user_id,
                    AutonomyRunRecord.assignment_epoch == epoch,
                )
            )
            if run is None:
                raise
            return run, False
        self.add_event(
            session,
            run,
            "assignment_detected",
            {"assignment_epoch": epoch, "cancelled": cancelled, "reason": reason},
        )
        return run, True

    @staticmethod
    def add_event(
        session: AsyncSession,
        run: AutonomyRunRecord,
        kind: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        session.add(
            AutonomyRunEventRecord(
                run_id=run.id,
                kind=kind,
                state=run.state,
                payload_json=payload or {},
            )
        )

    @staticmethod
    def schedule_retry(run: AutonomyRunRecord, blocker_code: str) -> bool:
        """Schedule one of the three pre-mutation retries; return False when exhausted."""
        retry_index = max(run.attempt_count - 1, 0)
        if retry_index >= len(RETRY_DELAYS):
            return False
        run.state = "retry_wait"
        run.blocker_code = blocker_code
        run.next_attempt_at = datetime.now(UTC) + timedelta(seconds=RETRY_DELAYS[retry_index])
        return True

    @staticmethod
    def _latest_event_key(events: list[TaskLifetimeEventDTO]) -> str | None:
        return next((item.event_key for item in reversed(events) if item.event_key), None)

    @staticmethod
    def _assignment_event_key(
        task_id: int,
        bot_user_id: int,
        events: list[TaskLifetimeEventDTO],
        display_name: str,
        fallback: str | None,
    ) -> str:
        needle = " ".join(display_name.casefold().split())
        for event in reversed(events):
            executors = " ".join((event.executors or "").casefold().split())
            if needle and needle in executors and event.event_key:
                return event.event_key
        raw = f"{task_id}:{bot_user_id}:{fallback or 'observed'}"
        return "observed:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _assignment_seen_since(
        events: list[TaskLifetimeEventDTO],
        previous_event_key: str | None,
        display_name: str,
    ) -> bool:
        needle = " ".join(display_name.casefold().split())
        if not needle:
            return False
        after_previous = previous_event_key is None
        for event in events:
            if not after_previous:
                if event.event_key == previous_event_key:
                    after_previous = True
                continue
            executors = " ".join((event.executors or "").casefold().split())
            if needle in executors:
                return True
        return False


class AutonomyFailureReporter:
    """Creates and delivers one safe private note per run/blocker."""

    SAFE_REASONS = {
        "ambiguous_case": "сценарий не удалось определить однозначно",
        "unsupported_case": "для заявки нет поддерживаемого автономного сценария",
        "clarification_exhausted": "не удалось получить обязательные данные",
        "preflight_failed": "техническая предварительная проверка не пройдена",
        "service_binding_failed": "сервисная привязка не подтверждена",
        "worker_unavailable": "исполнитель технического действия недоступен",
        "retries_exhausted": "повторные безопасные попытки исчерпаны",
        "unknown_outcome": "результат технического действия нельзя подтвердить",
        "status_unknown": "актуальный статус заявки не подтверждён",
    }

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        run: AutonomyRunRecord,
        phase: str,
        blocker_code: str,
    ) -> AutonomyCommentOutboxRecord:
        dedup_key = f"{run.id}:{blocker_code}"
        existing = await session.scalar(
            select(AutonomyCommentOutboxRecord).where(
                AutonomyCommentOutboxRecord.dedup_key == dedup_key
            )
        )
        if existing is not None:
            return existing
        safe_reason = self.SAFE_REASONS.get(blocker_code, "возникла безопасно остановившая выполнение ошибка")
        text = (
            f"[AUTO:{run.id}] Автономное решение не выполнено. "
            f"Этап: {phase}. Причина: {safe_reason}. Статус заявки не изменён."
        )
        record = AutonomyCommentOutboxRecord(
            run_id=run.id,
            task_id=run.task_id,
            dedup_key=dedup_key,
            phase=phase,
            blocker_code=blocker_code,
            comment=text,
            state="pending",
            next_attempt_at=datetime.now(UTC),
        )
        session.add(record)
        await session.flush()
        return record

    async def deliver_pending(
        self,
        session: AsyncSession,
        *,
        client: IntraServiceClient,
        auth_b64: str,
        limit: int = 50,
    ) -> int:
        now = datetime.now(UTC)
        records = list(
            (
                await session.scalars(
                    select(AutonomyCommentOutboxRecord)
                    .where(
                        AutonomyCommentOutboxRecord.state.in_(["pending", "retry_wait"]),
                        AutonomyCommentOutboxRecord.next_attempt_at <= now,
                    )
                    .order_by(AutonomyCommentOutboxRecord.created_at.asc())
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            ).all()
        )
        delivered = 0
        for record in records:
            try:
                lifetime = await client.get_task_lifetime(
                    task_id=record.task_id,
                    auth_b64=auth_b64,
                )
                already_published = any(
                    item.is_private and (item.comment or "").strip() == record.comment
                    for item in lifetime
                )
                if already_published:
                    record.state = "published"
                    record.published_at = now
                    record.last_error = None
                    delivered += 1
                    continue
                await client.add_task_comment(
                    task_id=record.task_id,
                    comment=record.comment,
                    is_private=True,
                    auth_b64=auth_b64,
                )
            except Exception as exc:
                record.attempts += 1
                record.state = "retry_wait"
                record.last_error = sanitize_secret_text(type(exc).__name__)
                delay = min(3600, 30 * (2 ** min(record.attempts - 1, 7)))
                record.next_attempt_at = now + timedelta(seconds=delay)
            else:
                record.state = "published"
                record.published_at = now
                record.last_error = None
                delivered += 1
        await session.flush()
        return delivered


class ExecutionPermitService:
    """Issues short-lived permits only for exact, successful preflight bindings."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds

    async def issue(
        self,
        session: AsyncSession,
        *,
        run: AutonomyRunRecord,
        action_plan_id: UUID,
    ) -> list[ExecutionPermitRecord]:
        plan_record = await session.scalar(
            select(ActionPlanRecord)
            .where(ActionPlanRecord.id == action_plan_id)
            .with_for_update()
        )
        if plan_record is None:
            raise LookupError("action_plan_not_found")
        plan = ActionPlan.model_validate(plan_record.plan_json)
        if plan.task_id != run.task_id or plan_record.autonomy_run_id != run.id:
            raise ValueError("autonomy_plan_binding_mismatch")
        if compute_action_plan_hash(plan) != plan.plan_hash or plan.plan_hash != plan_record.plan_hash:
            raise ValueError("action_plan_hash_mismatch")
        now = datetime.now(UTC)
        permits: list[ExecutionPermitRecord] = []
        for action in sorted(plan.actions, key=lambda item: item.sequence_no):
            params_hash = canonical_params_hash(action.params)
            preflight = await session.scalar(
                select(ActionPreflightRecord)
                .where(
                    ActionPreflightRecord.action_plan_id == plan.id,
                    ActionPreflightRecord.action_id == action.id,
                    ActionPreflightRecord.capability_key == action.capability_key,
                    ActionPreflightRecord.snapshot_hash == plan.snapshot_hash,
                    ActionPreflightRecord.plan_hash == plan.plan_hash,
                    ActionPreflightRecord.params_hash == params_hash,
                    ActionPreflightRecord.status.in_(["passed", "not_applicable"]),
                    ActionPreflightRecord.expires_at > now,
                )
                .order_by(ActionPreflightRecord.created_at.desc())
                .limit(1)
            )
            if preflight is None:
                raise ValueError("action_preflight_not_passed")
            existing = await session.scalar(
                select(ExecutionPermitRecord).where(
                    ExecutionPermitRecord.action_plan_id == plan.id,
                    ExecutionPermitRecord.action_id == action.id,
                )
            )
            if existing is not None:
                permits.append(existing)
                continue
            preflight_expiry = preflight.expires_at
            if preflight_expiry.tzinfo is None:
                preflight_expiry = preflight_expiry.replace(tzinfo=UTC)
            permit = ExecutionPermitRecord(
                run_id=run.id,
                task_id=run.task_id,
                action_plan_id=plan.id,
                action_id=action.id,
                capability_key=action.capability_key,
                assignment_event_key=run.assignment_event_key,
                snapshot_hash=plan.snapshot_hash,
                plan_hash=plan.plan_hash,
                params_hash=params_hash,
                preflight_id=preflight.id,
                checks_json={"preflight_status": preflight.status},
                expires_at=min(preflight_expiry, now + timedelta(seconds=self.ttl_seconds)),
            )
            session.add(permit)
            permits.append(permit)
        authorized = plan.model_copy(update={"state": ActionPlanState.authorized})
        plan_record.state = ActionPlanState.authorized.value
        plan_record.plan_json = authorized.model_dump(mode="json")
        run.action_plan_id = plan.id
        run.snapshot_hash = plan.snapshot_hash
        run.state = "ready"
        run.blocker_code = None
        run.next_attempt_at = None
        await session.flush()
        return permits
