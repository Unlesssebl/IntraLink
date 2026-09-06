"""Durable ticket-cycle registration and global autopilot control."""

from __future__ import annotations

import enum
import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import (
    AutopilotSetting,
    AutopilotSettingEvent,
    CommandEvent,
    CommandRecord,
    TicketRun,
    TicketRunEvent,
    TriageTemplate,
)
from app.config import settings
from app.services.template_engine import render_template_strict

logger = logging.getLogger(__name__)

AUTOPILOT_SETTING_KEY = "global"
REQUIRED_AUTOPILOT_TEMPLATES = frozenset(
    {
        "printer_ip_clarify",
        "pc_offline",
        "ticket_timeout_cancel",
        "ticket_not_relevant",
        "autopilot_unsupported_cancel",
        "autopilot_execution_failed_cancel",
        "resolved_standard",
    }
)

AUTOPILOT_TEMPLATE_SPECS: dict[str, tuple[int, dict[str, Any]]] = {
    "printer_ip_clarify": (settings.STATUS_WAITING_ID, {}),
    "pc_offline": (settings.STATUS_WAITING_ID, {"pc_name": "PC-TEST"}),
    "ticket_timeout_cancel": (settings.STATUS_CANCELLED_ID, {}),
    "ticket_not_relevant": (settings.STATUS_CANCELLED_ID, {}),
    "autopilot_unsupported_cancel": (
        settings.STATUS_CANCELLED_ID,
        {"reason": "проверка"},
    ),
    "autopilot_execution_failed_cancel": (
        settings.STATUS_CANCELLED_ID,
        {"reason": "проверка"},
    ),
    "resolved_standard": (settings.STATUS_COMPLETED_ID, {}),
}


class TicketRunMode(str, enum.Enum):
    MANUAL = "manual"
    AUTOPILOT = "autopilot"


class TicketRunState(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_ANSWER = "waiting_answer"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    SYSTEM_ERROR = "system_error"
    COMPLETED = "completed"


class TicketRunOutcome(str, enum.Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXTERNALLY_STOPPED = "externally_stopped"


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    run: TicketRun | None
    created: bool
    reason: str


def task_executor_ids(task: dict[str, Any]) -> set[int]:
    """Normalize both executor shapes returned by different IntraService endpoints."""
    result: set[int] = set()
    direct = task.get("ExecutorId")
    try:
        if direct is not None:
            result.add(int(direct))
    except (TypeError, ValueError):
        pass

    raw = task.get("ExecutorIds")
    if isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        values = str(raw or "").split(",")
    for value in values:
        try:
            result.add(int(str(value).strip()))
        except (TypeError, ValueError):
            continue
    return result


class TicketRunService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_global_setting(self, *, create: bool = True) -> AutopilotSetting | None:
        setting = await self.db.get(AutopilotSetting, AUTOPILOT_SETTING_KEY)
        if setting is None and create:
            setting = AutopilotSetting(
                key=AUTOPILOT_SETTING_KEY,
                enabled=False,
                version=1,
                updated_by="system:default",
            )
            self.db.add(setting)
            await self.db.flush()
            self.db.add(
                AutopilotSettingEvent(
                    setting_key=setting.key,
                    enabled=False,
                    version=1,
                    actor="system:default",
                    reason="safe_default",
                )
            )
        return setting

    async def set_global_enabled(
        self,
        *,
        enabled: bool,
        actor: str,
        reason: str | None = None,
        expected_version: int | None = None,
    ) -> AutopilotSetting:
        setting = await self.db.scalar(
            select(AutopilotSetting)
            .where(AutopilotSetting.key == AUTOPILOT_SETTING_KEY)
            .with_for_update()
        )
        if setting is None:
            setting = await self.get_global_setting()
        assert setting is not None
        if expected_version is not None and setting.version != expected_version:
            raise ValueError("autopilot_setting_version_conflict")
        if enabled:
            readiness = await self.template_readiness()
            if not readiness["ready"]:
                raise ValueError(
                    "autopilot_templates_not_ready:" + ",".join(readiness["missing"])
                )
        if setting.enabled == enabled:
            await self.db.commit()
            await self.db.refresh(setting)
            return setting

        setting.enabled = enabled
        setting.version += 1
        setting.updated_by = actor
        self.db.add(
            AutopilotSettingEvent(
                setting_key=setting.key,
                enabled=enabled,
                version=setting.version,
                actor=actor,
                reason=(reason or "").strip() or None,
            )
        )
        if not enabled:
            runs = list(
                (
                    await self.db.scalars(
                        select(TicketRun).where(
                            TicketRun.completed_at.is_(None),
                            TicketRun.mode == TicketRunMode.AUTOPILOT.value,
                        )
                    )
                ).all()
            )
            for run in runs:
                if run.state == TicketRunState.PAUSED.value and run.pause_reason == "operator":
                    continue
                previous_state = run.state
                run.state = TicketRunState.PAUSED.value
                run.pause_reason = "global_disabled"
                run.version += 1
                run.updated_by = actor
                await self._append_run_event(
                    run,
                    event_type="run_paused",
                    actor=actor,
                    details={"reason": "global_disabled", "previous_state": previous_state},
                )
                await self._cancel_unstarted_commands(
                    run.id, actor=actor, reason="global_autopilot_disabled"
                )
        await self.db.commit()
        await self.db.refresh(setting)
        return setting

    async def template_readiness(self) -> dict[str, Any]:
        invalid: list[str] = []
        for template_key, (status_id, context) in AUTOPILOT_TEMPLATE_SPECS.items():
            try:
                await render_template_strict(
                    self.db,
                    template_key,
                    context,
                    expected_status_id=status_id,
                )
            except ValueError:
                invalid.append(template_key)
        return {"ready": not invalid, "missing": sorted(invalid)}

    async def register_assignment(
        self,
        *,
        task: dict[str, Any],
        assistant_user_id: int,
        open_status_id: int,
        trigger_key: str | None = None,
        trigger_kind: str = "initial_assignment",
        actor: str = "poller",
    ) -> RegistrationResult:
        """Register one assignment basis; no external action is executed here."""
        try:
            task_id = int(task.get("Id") or 0)
            status_id = int(task.get("StatusId") or 0)
        except (TypeError, ValueError):
            return RegistrationResult(None, False, "invalid_task")
        if task_id <= 0:
            return RegistrationResult(None, False, "invalid_task")
        if status_id != open_status_id:
            return RegistrationResult(None, False, "status_not_open")
        if assistant_user_id not in task_executor_ids(task):
            return RegistrationResult(None, False, "assistant_not_assigned")

        basis = (trigger_key or f"initial-assignment:{task_id}").strip()
        if not basis or len(basis) > 160:
            return RegistrationResult(None, False, "invalid_trigger")

        existing = await self.db.scalar(
            select(TicketRun).where(
                TicketRun.task_id == task_id,
                TicketRun.trigger_key == basis,
            )
        )
        if existing is not None:
            if (
                existing.completed_at is None
                and existing.state
                in {TicketRunState.PENDING.value, TicketRunState.PAUSED.value}
                and existing.pause_reason == "global_disabled"
            ):
                setting = await self.get_global_setting()
                if setting is not None and setting.enabled:
                    existing.state = TicketRunState.RUNNING.value
                    existing.pause_reason = None
                    existing.version += 1
                    existing.updated_by = actor
                    sequence = (
                        await self.db.scalar(
                            select(func.max(TicketRunEvent.sequence)).where(
                                TicketRunEvent.ticket_run_id == existing.id
                            )
                        )
                        or 0
                    ) + 1
                    self.db.add(
                        TicketRunEvent(
                            ticket_run_id=existing.id,
                            sequence=sequence,
                            event_type="run_resumed",
                            actor=actor,
                            details_json={"reason": "global_enabled_and_assignment_revalidated"},
                        )
                    )
                    await self.db.commit()
                    await self.db.refresh(existing)
                    return RegistrationResult(existing, False, "resumed")
            return RegistrationResult(existing, False, "duplicate_trigger")

        active = await self.db.scalar(
            select(TicketRun).where(
                TicketRun.task_id == task_id,
                TicketRun.completed_at.is_(None),
            )
        )
        if active is not None:
            return RegistrationResult(active, False, "active_run_exists")

        setting = await self.get_global_setting()
        assert setting is not None
        state = TicketRunState.RUNNING if setting.enabled else TicketRunState.PENDING
        pause_reason = None if setting.enabled else "global_disabled"
        run = TicketRun(
            task_id=task_id,
            mode=TicketRunMode.AUTOPILOT.value,
            state=state.value,
            trigger_kind=trigger_kind,
            trigger_key=basis,
            trigger_snapshot_json={
                "task_id": task_id,
                "status_id": status_id,
                "assistant_user_id": assistant_user_id,
                "executor_ids": sorted(task_executor_ids(task)),
            },
            current_step="validate_request",
            pause_reason=pause_reason,
            created_by=actor,
            updated_by=actor,
        )
        self.db.add(run)
        try:
            await self.db.flush()
            self.db.add(
                TicketRunEvent(
                    ticket_run_id=run.id,
                    sequence=1,
                    event_type="run_created",
                    actor=actor,
                    details_json={
                        "mode": run.mode,
                        "state": run.state,
                        "trigger_kind": trigger_kind,
                        "trigger_key": basis,
                        "autopilot_enabled": setting.enabled,
                    },
                )
            )
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            winner = await self.db.scalar(
                select(TicketRun).where(
                    TicketRun.task_id == task_id,
                    TicketRun.completed_at.is_(None),
                )
            )
            if winner is None:
                winner = await self.db.scalar(
                    select(TicketRun).where(
                        TicketRun.task_id == task_id,
                        TicketRun.trigger_key == basis,
                    )
                )
            if winner is None:
                raise
            return RegistrationResult(winner, False, "concurrent_registration")
        await self.db.refresh(run)
        return RegistrationResult(run, True, "created")

    async def list_resumable(self, *, limit: int = 100) -> list[TicketRun]:
        """Return persisted cycles whose next step may be evaluated by the runner."""
        statement = (
            select(TicketRun)
            .where(
                TicketRun.completed_at.is_(None),
                TicketRun.mode == TicketRunMode.AUTOPILOT.value,
                TicketRun.state.in_(
                    [
                        TicketRunState.RUNNING.value,
                        TicketRunState.WAITING_ANSWER.value,
                        TicketRunState.WAITING_APPROVAL.value,
                    ]
                ),
            )
            .order_by(TicketRun.updated_at, TicketRun.created_at)
            .limit(limit)
        )
        return list((await self.db.scalars(statement)).all())

    async def list_active(
        self, *, limit: int = 200, after_id: uuid.UUID | None = None
    ) -> list[TicketRun]:
        """Return one stable UUID-keyset page of unfinished cycles."""
        statement = select(TicketRun).where(TicketRun.completed_at.is_(None))
        if after_id is not None:
            statement = statement.where(TicketRun.id > after_id)
        return list(
            (
                await self.db.scalars(
                    statement.order_by(TicketRun.id).limit(limit)
                )
            ).all()
        )

    async def get_latest_for_task(self, task_id: int) -> TicketRun | None:
        return await self.db.scalar(
            select(TicketRun)
            .where(TicketRun.task_id == task_id)
            .order_by(TicketRun.created_at.desc())
            .limit(1)
        )

    async def _append_run_event(
        self,
        run: TicketRun,
        *,
        event_type: str,
        actor: str,
        details: dict[str, Any],
    ) -> None:
        sequence = (
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
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                details_json=details,
            )
        )

    async def _cancel_unstarted_commands(
        self, run_id: uuid.UUID, *, actor: str, reason: str
    ) -> None:
        commands = list(
            (
                await self.db.scalars(
                    select(CommandRecord).where(
                        CommandRecord.ticket_run_id == run_id,
                        CommandRecord.status.in_(["queued", "awaiting_approval"]),
                    )
                )
            ).all()
        )
        now = dt.datetime.now(dt.timezone.utc)
        for command in commands:
            command.status = "cancelled"
            command.error_message = reason
            command.completed_at = now
            command.version += 1
            command_sequence = (
                await self.db.scalar(
                    select(func.max(CommandEvent.sequence)).where(
                        CommandEvent.command_id == command.id
                    )
                )
                or 0
            ) + 1
            self.db.add(
                CommandEvent(
                    command_id=command.id,
                    sequence=command_sequence,
                    event_type="cancelled",
                    actor=actor,
                    details_json={"reason": reason},
                )
            )

    async def get_events(self, run_id: uuid.UUID) -> list[TicketRunEvent]:
        return list(
            (
                await self.db.scalars(
                    select(TicketRunEvent)
                    .where(TicketRunEvent.ticket_run_id == run_id)
                    .order_by(TicketRunEvent.sequence)
                )
            ).all()
        )

    async def start_manual(
        self,
        *,
        task_id: int,
        status_id: int,
        allowed_status_ids: set[int],
        actor: str,
        trigger_key: str,
    ) -> TicketRun:
        if status_id not in allowed_status_ids:
            raise ValueError("ticket_status_not_active")
        active = await self.db.scalar(
            select(TicketRun).where(
                TicketRun.task_id == task_id,
                TicketRun.completed_at.is_(None),
            )
        )
        if active is not None:
            return active
        run = TicketRun(
            task_id=task_id,
            mode=TicketRunMode.MANUAL.value,
            state=TicketRunState.RUNNING.value,
            trigger_kind="explicit_start",
            trigger_key=trigger_key,
            trigger_snapshot_json={"task_id": task_id, "status_id": status_id},
            current_step="manual_work",
            created_by=actor,
            updated_by=actor,
        )
        self.db.add(run)
        try:
            await self.db.flush()
            self.db.add(
                TicketRunEvent(
                    ticket_run_id=run.id,
                    sequence=1,
                    event_type="run_created",
                    actor=actor,
                    details_json={"mode": run.mode, "state": run.state},
                )
            )
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            winner = await self.db.scalar(
                select(TicketRun).where(
                    TicketRun.task_id == task_id,
                    TicketRun.completed_at.is_(None),
                )
            )
            if winner is None:
                raise
            return winner
        await self.db.refresh(run)
        return run

    async def update_control(
        self,
        *,
        run_id: uuid.UUID,
        action: str,
        actor: str,
        expected_version: int,
        target_mode: TicketRunMode | None = None,
    ) -> TicketRun:
        run = await self.db.scalar(
            select(TicketRun).where(TicketRun.id == run_id).with_for_update()
        )
        if run is None:
            raise ValueError("run_not_found")
        if run.completed_at is not None:
            raise ValueError("run_completed")
        if run.version != expected_version:
            raise ValueError("run_version_conflict")

        details: dict[str, Any] = {"action": action, "previous_state": run.state}
        event_type = "run_updated"
        if action == "pause":
            if run.state == TicketRunState.PAUSED.value and run.pause_reason == "operator":
                return run
            run.state = TicketRunState.PAUSED.value
            run.pause_reason = "operator"
            event_type = "run_paused"
            await self._cancel_unstarted_commands(
                run.id, actor=actor, reason="ticket_run_paused"
            )
        elif action == "resume":
            setting = await self.get_global_setting()
            if run.mode == TicketRunMode.AUTOPILOT.value and not (setting and setting.enabled):
                raise ValueError("autopilot_disabled")
            run.state = TicketRunState.RUNNING.value
            run.pause_reason = None
            run.error_code = None
            run.error_message = None
            event_type = "run_resumed"
        elif action == "switch_mode" and target_mode is not None:
            if target_mode == TicketRunMode.AUTOPILOT:
                setting = await self.get_global_setting()
                if not (setting and setting.enabled):
                    raise ValueError("autopilot_disabled")
            details["previous_mode"] = run.mode
            run.mode = target_mode.value
            run.state = TicketRunState.RUNNING.value
            run.pause_reason = None
            run.current_step = (
                run.current_step
                if target_mode == TicketRunMode.AUTOPILOT
                else "manual_work"
            )
            details["mode"] = run.mode
            event_type = "run_mode_changed"
            if target_mode == TicketRunMode.MANUAL:
                await self._cancel_unstarted_commands(
                    run.id, actor=actor, reason="ticket_run_switched_to_manual"
                )
        else:
            raise ValueError("invalid_control_action")

        run.version += 1
        run.updated_by = actor
        details["state"] = run.state
        sequence = (
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
                sequence=sequence,
                event_type=event_type,
                actor=actor,
                details_json=details,
            )
        )
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def finish_external(
        self,
        *,
        run_id: uuid.UUID,
        actor: str,
        status_id: int,
    ) -> TicketRun:
        run = await self.db.scalar(
            select(TicketRun).where(TicketRun.id == run_id).with_for_update()
        )
        if run is None:
            raise ValueError("run_not_found")
        if run.completed_at is not None:
            return run
        run.state = TicketRunState.COMPLETED.value
        run.outcome = TicketRunOutcome.EXTERNALLY_STOPPED.value
        run.completed_at = dt.datetime.now(dt.timezone.utc)
        run.version += 1
        run.updated_by = actor
        sequence = (
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
                sequence=sequence,
                event_type="run_externally_stopped",
                actor=actor,
                details_json={"status_id": status_id},
            )
        )
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def pause_automatic(
        self,
        *,
        run_id: uuid.UUID,
        actor: str,
        reason: str,
    ) -> TicketRun:
        run = await self.db.scalar(
            select(TicketRun).where(TicketRun.id == run_id).with_for_update()
        )
        if run is None:
            raise ValueError("run_not_found")
        if run.completed_at is not None:
            return run
        if run.state == TicketRunState.PAUSED.value and run.pause_reason == reason:
            return run
        previous_state = run.state
        run.state = TicketRunState.PAUSED.value
        run.pause_reason = reason
        run.version += 1
        run.updated_by = actor
        await self._cancel_unstarted_commands(run.id, actor=actor, reason=reason)
        await self._append_run_event(
            run,
            event_type="run_paused",
            actor=actor,
            details={"reason": reason, "previous_state": previous_state},
        )
        await self.db.commit()
        await self.db.refresh(run)
        return run


async def register_observed_assignments(
    *,
    tasks: list[dict[str, Any]],
    assistant_user_id: int,
    open_status_id: int,
    actor: str = "poller",
) -> list[RegistrationResult]:
    """Use an isolated transaction per task so one conflict cannot discard other registrations."""
    from app.database.db import AsyncSessionLocal

    results: list[RegistrationResult] = []
    for task in tasks:
        try:
            async with AsyncSessionLocal() as db:
                results.append(
                    await TicketRunService(db).register_assignment(
                        task=task,
                        assistant_user_id=assistant_user_id,
                        open_status_id=open_status_id,
                        actor=actor,
                    )
                )
        except Exception:
            logger.exception(
                "Не удалось зарегистрировать назначение для заявки %s",
                task.get("Id"),
            )
    return results
