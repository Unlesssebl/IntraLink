"""Deterministic first-scenario runner built on Command API v2."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import AutopilotScenario, CommandRecord, TicketRun
from app.services.command_service import CommandService
from app.services.decision_journal import DecisionJournalService
from app.services.lifecycle.intent_analyzer import IntentAnalyzer
from app.services.template_engine import detect_service_redirect, render_template_strict
from app.services.ticket_runs import TicketRunService, TicketRunState, task_executor_ids


class TicketRunRunner:
    """Advance at most one durable step; callers may safely invoke it repeatedly."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.runs = TicketRunService(db)

    @staticmethod
    def extract_printer_parameters(task: dict[str, Any]) -> tuple[str, str]:
        pc_name = ""
        printer_address = ""
        for field in task.get("CustomFields", []) or []:
            field_id = field.get("CustomFieldId") or field.get("FieldId")
            value = str(field.get("Value") or "").strip()
            if field_id == settings.PRINTER_PC_CUSTOM_FIELD_ID and value:
                pc_name = value
            elif field_id == settings.PRINTER_IP_CUSTOM_FIELD_ID and value:
                printer_address = value
        if not pc_name or not printer_address:
            extracted = IntentAnalyzer.analyze_fast_regex(
                f"{task.get('Name', '')} {task.get('Description', '')}"
            )
            if extracted:
                pc_name = pc_name or extracted.extracted_pc or ""
                printer_address = printer_address or extracted.extracted_ip or ""
        return pc_name, printer_address

    @staticmethod
    def is_supported_printer_installation(
        task: dict[str, Any], pc_name: str, printer_address: str
    ) -> bool:
        text = f"{task.get('Name', '')} {task.get('Description', '')}".lower()
        printer_topic = any(
            token in text
            for token in (
                "принтер",
                "мфу",
                "kyocera",
                "ecosys",
                "laserjet",
                "canon",
                "xerox",
                "pantum",
            )
        )
        installation_intent = any(
            token in text
            for token in (
                "установ",
                "подключ",
                "настро",
                "добавить",
            )
        )
        return printer_topic and (installation_intent or bool(pc_name and printer_address))

    async def _command(self, run: TicketRun, step: str) -> CommandRecord | None:
        return await self.db.scalar(
            select(CommandRecord).where(
                CommandRecord.ticket_run_id == run.id,
                CommandRecord.idempotency_key == f"ticket-run:{run.id}:{step}",
            )
        )

    async def _record_step(
        self,
        run: TicketRun,
        *,
        step: str,
        state: str,
        actor: str,
        command: CommandRecord | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        event_details: dict[str, Any] | None = None,
    ) -> TicketRun:
        run.current_step = step
        run.state = state
        run.error_code = error_code
        run.error_message = error_message
        run.version += 1
        run.updated_by = actor
        await self.runs._append_run_event(
            run,
            event_type="run_step_changed" if state != TicketRunState.SYSTEM_ERROR.value else "run_system_error",
            actor=actor,
            details={
                "step": step,
                "state": state,
                "command_id": str(command.id) if command else None,
                "command_status": command.status if command else None,
                "error_code": error_code,
                **(event_details or {}),
            },
        )
        await self.db.commit()
        await self.db.refresh(run)
        return run

    async def _create_command(
        self,
        run: TicketRun,
        *,
        step: str,
        action: str,
        target: dict[str, Any],
        parameters: dict[str, Any],
        actor: str,
    ) -> CommandRecord:
        decision = await DecisionJournalService(self.db).record_operational(
            task_id=run.task_id,
            ticket_run_id=run.id,
            action=action,
            target=target,
            parameters=parameters,
            actor=actor,
        )
        command, _duplicate = await CommandService(self.db).create(
            action=action,
            target=target,
            parameters=parameters,
            idempotency_key=f"ticket-run:{run.id}:{step}",
            initiator=actor,
            source="autopilot",
            priority=5,
            ticket_run_id=run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )
        await self._record_step(
            run,
            step=step,
            state=(
                TicketRunState.WAITING_APPROVAL.value
                if command.status == "awaiting_approval"
                else TicketRunState.RUNNING.value
            ),
            actor=actor,
            command=command,
            event_details={
                "action": action,
                "template_key": parameters.get("template_key"),
                "cancellation_reason": parameters.get("cancellation_reason"),
                "rendered_comment_snapshot": (
                    parameters.get("comment") if step.startswith("cancel_") else None
                ),
            },
        )
        return command

    async def advance(
        self,
        *,
        run_id: uuid.UUID,
        task: dict[str, Any],
        comments: list[dict[str, Any]] | None = None,
        actor: str = "autopilot",
    ) -> TicketRun:
        run = await self.db.get(TicketRun, run_id)
        if run is None:
            raise ValueError("run_not_found")
        if run.completed_at is not None or run.mode != "autopilot":
            return run
        if run.state in {TicketRunState.PAUSED.value, TicketRunState.SYSTEM_ERROR.value}:
            return run
        if run.state == TicketRunState.WAITING_ANSWER.value:
            return await self._advance_waiting_answer(
                run=run, task=task, comments=comments or [], actor=actor
            )

        pc_name, printer_address = self.extract_printer_parameters(task)
        executor_ids = ",".join(str(value) for value in sorted(task_executor_ids(task)))
        collected = (run.trigger_snapshot_json or {}).get("collected_params", {})
        pc_name = pc_name or str(collected.get("pc_name") or "")
        printer_address = printer_address or str(collected.get("printer_address") or "")
        step = run.current_step or "validate_request"

        if step == "validate_request":
            redirect = detect_service_redirect(task)
            if redirect:
                scenario = await self.db.scalar(
                    select(AutopilotScenario).where(
                        AutopilotScenario.service_id == int(task.get("ServiceId") or 0),
                        AutopilotScenario.scenario_key == "printer_installation",
                        AutopilotScenario.enabled.is_(True),
                    )
                )
                targets = (scenario.config_json or {}).get("redirect_targets", {}) if scenario else {}
                target = targets.get(str(redirect.get("target_root"))) or {}
                if isinstance(target, str):
                    target = {"url": target}
                target_url = str(target.get("url") or "").strip()
                if not target_url:
                    run.pause_reason = "redirect_target_unavailable"
                    return await self._record_step(
                        run,
                        step="validate_request",
                        state=TicketRunState.PAUSED.value,
                        actor=actor,
                        error_code="redirect_target_unavailable",
                        error_message="Целевой сервис определён, но ссылка на него не настроена",
                        event_details={"requires_attention": True, "redirect": redirect},
                    )
                target_name = str(
                    target.get("name") or redirect.get("target_service_name") or "целевой сервис"
                )
                return await self._create_cancellation(
                    run,
                    step="cancel_redirect",
                    template_key="wrong_service",
                    context={"target_service": f"{target_name} — {target_url}"},
                    actor=actor,
                )
            if not self.is_supported_printer_installation(task, pc_name, printer_address):
                run.pause_reason = "unsupported_scenario"
                return await self._record_step(
                    run,
                    step="validate_request",
                    state=TicketRunState.PAUSED.value,
                    actor=actor,
                    error_code="unsupported_scenario",
                    error_message=(
                        "Заявка не относится к поддерживаемому сценарию установки принтера"
                    ),
                    event_details={"requires_attention": True},
                )
            if not pc_name or not printer_address:
                if run.clarification_count >= 2:
                    run.pause_reason = "clarification_unresolved"
                    return await self._record_step(
                        run,
                        step="validate_request",
                        state=TicketRunState.PAUSED.value,
                        actor=actor,
                        error_code="clarification_unresolved",
                        error_message="Не удалось однозначно извлечь обязательные реквизиты",
                        event_details={"requires_attention": True},
                    )
                try:
                    rendered = await render_template_strict(
                        self.db,
                        "printer_ip_clarify",
                        {},
                        expected_status_id=settings.STATUS_WAITING_ID,
                    )
                except ValueError as exc:
                    return await self._record_step(
                        run,
                        step="validate_request",
                        state=TicketRunState.SYSTEM_ERROR.value,
                        actor=actor,
                        error_code="template_invalid",
                        error_message=str(exc),
                    )
                await self._create_command(
                    run,
                    step=f"request_clarification_{run.clarification_count + 1}",
                    action="apply_triage",
                    target={"task_id": run.task_id},
                    parameters={
                        "task_ids": [run.task_id],
                        "status_id": rendered["status_id"],
                        "comment": rendered["comment"],
                        "expenses": rendered["expenses"],
                        "template_key": rendered["template_key"],
                        "executor_ids": executor_ids,
                    },
                    actor=actor,
                )
                return run
            await self._create_command(
                run,
                step="diagnose_host",
                action="diagnose_host",
                target={"task_id": run.task_id, "host": pc_name},
                parameters={},
                actor=actor,
            )
            return run

        command = await self._command(run, step)
        if command is None:
            return await self._record_step(
                run,
                step=step,
                state=TicketRunState.SYSTEM_ERROR.value,
                actor=actor,
                error_code="command_missing",
                error_message=f"Linked command for step '{step}' is missing",
            )
        if command.status == "awaiting_approval":
            if run.state != TicketRunState.WAITING_APPROVAL.value:
                await self._record_step(
                    run,
                    step=step,
                    state=TicketRunState.WAITING_APPROVAL.value,
                    actor=actor,
                    command=command,
                )
            return run
        if command.status in {"queued", "running"}:
            return run
        if command.status in {"rejected", "cancelled"}:
            run.pause_reason = "approval_rejected" if command.status == "rejected" else "command_cancelled"
            return await self._record_step(
                run,
                step=step,
                state=TicketRunState.PAUSED.value,
                actor=actor,
                command=command,
            )
        if command.status in {"failed", "needs_review"}:
            run.pause_reason = (
                "execution_failed" if command.status == "failed" else "result_needs_review"
            )
            return await self._record_step(
                run,
                step=step,
                state=TicketRunState.PAUSED.value,
                actor=actor,
                command=command,
                error_code=run.pause_reason,
                error_message=command.error_message or "Command result requires review",
                event_details={"requires_attention": True},
            )

        if (
            step.startswith("request_clarification_") or step == "request_pc_online"
        ) and command.status == "succeeded":
            run.clarification_count += 1
            run.waiting_reason = (
                "pc_offline"
                if step == "request_pc_online"
                else "missing_printer_parameters"
            )
            run.waiting_until = (
                command.completed_at or dt.datetime.now(dt.timezone.utc)
            ) + dt.timedelta(hours=72)
            return await self._record_step(
                run,
                step="wait_for_answer",
                state=TicketRunState.WAITING_ANSWER.value,
                actor=actor,
                command=command,
            )

        if step == "diagnose_host" and command.status == "succeeded":
            result = command.result_json or {}
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            diagnostics = (
                payload.get("diagnostics")
                if isinstance(payload.get("diagnostics"), dict)
                else {}
            )
            is_online = result.get("is_online", diagnostics.get("is_online"))
            if is_online is not True:
                try:
                    rendered = await render_template_strict(
                        self.db,
                        "pc_offline",
                        {"pc_name": pc_name},
                        expected_status_id=settings.STATUS_WAITING_ID,
                    )
                except ValueError as exc:
                    return await self._record_step(
                        run,
                        step=step,
                        state=TicketRunState.SYSTEM_ERROR.value,
                        actor=actor,
                        error_code="template_invalid",
                        error_message=str(exc),
                    )
                await self._create_command(
                    run,
                    step="request_pc_online",
                    action="apply_triage",
                    target={"task_id": run.task_id},
                    parameters={
                        "task_ids": [run.task_id],
                        "status_id": rendered["status_id"],
                        "comment": rendered["comment"],
                        "expenses": rendered["expenses"],
                        "template_key": rendered["template_key"],
                        "executor_ids": executor_ids,
                    },
                    actor=actor,
                )
                return run
            await self._create_command(
                run,
                step="install_printer",
                action="install_printer",
                target={"task_id": run.task_id, "pc_name": pc_name},
                parameters={
                    "printer_name": str(task.get("Name") or printer_address),
                    "printer_ip": printer_address,
                },
                actor=actor,
            )
            return run

        if step == "install_printer" and command.status == "succeeded":
            result = command.result_json or {}
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            verified = bool(
                result.get("verified")
                or payload.get("verified")
                or (result.get("installed") and result.get("verified"))
            )
            if not verified:
                return await self._record_step(
                    run,
                    step=step,
                    state=TicketRunState.SYSTEM_ERROR.value,
                    actor=actor,
                    command=command,
                    error_code="installation_not_verified",
                    error_message="Printer command succeeded without verified installation evidence",
                )
            try:
                rendered = await render_template_strict(
                    self.db,
                    "resolved_standard",
                    {},
                    expected_status_id=settings.STATUS_COMPLETED_ID,
                )
            except ValueError as exc:
                return await self._record_step(
                    run,
                    step=step,
                    state=TicketRunState.SYSTEM_ERROR.value,
                    actor=actor,
                    error_code="template_invalid",
                    error_message=str(exc),
                )
            await self._create_command(
                run,
                step="complete_ticket",
                action="apply_triage",
                target={"task_id": run.task_id},
                parameters={
                    "task_ids": [run.task_id],
                    "status_id": rendered["status_id"],
                    "comment": rendered["comment"],
                    "expenses": rendered["expenses"],
                    "verified_execution_job_id": str(command.id),
                    "template_key": rendered["template_key"],
                    "executor_ids": executor_ids,
                },
                actor=actor,
            )
            return run

        if step == "complete_ticket" and command.status == "succeeded":
            run.state = TicketRunState.COMPLETED.value
            run.outcome = "completed"
            run.completed_at = dt.datetime.now(dt.timezone.utc)
            return await self._record_step(
                run,
                step="completed",
                state=TicketRunState.COMPLETED.value,
                actor=actor,
                command=command,
            )
        if step.startswith("cancel_") and command.status == "succeeded":
            run.state = TicketRunState.COMPLETED.value
            run.outcome = "cancelled"
            run.completed_at = dt.datetime.now(dt.timezone.utc)
            return await self._record_step(
                run,
                step="cancelled",
                state=TicketRunState.COMPLETED.value,
                actor=actor,
                command=command,
            )
        return run

    async def _create_cancellation(
        self,
        run: TicketRun,
        *,
        step: str,
        template_key: str,
        context: dict[str, Any],
        actor: str,
    ) -> TicketRun:
        try:
            rendered = await render_template_strict(
                self.db,
                template_key,
                context,
                expected_status_id=settings.STATUS_CANCELLED_ID,
            )
        except ValueError as exc:
            return await self._record_step(
                run,
                step=run.current_step or "wait_for_answer",
                state=TicketRunState.SYSTEM_ERROR.value,
                actor=actor,
                error_code="template_invalid",
                error_message=str(exc),
            )
        if run.state != TicketRunState.RUNNING.value:
            await self._record_step(
                run,
                step=run.current_step or "wait_for_answer",
                state=TicketRunState.RUNNING.value,
                actor=actor,
            )
        await self._create_command(
            run,
            step=step,
            action="apply_triage",
            target={"task_id": run.task_id},
            parameters={
                "task_ids": [run.task_id],
                "status_id": rendered["status_id"],
                "comment": rendered["comment"],
                "expenses": rendered["expenses"],
                "template_key": rendered["template_key"],
                "cancellation_reason": context.get("reason") or template_key,
                "executor_ids": ",".join(
                    str(value)
                    for value in (run.trigger_snapshot_json or {}).get("executor_ids", [])
                ),
            },
            actor=actor,
        )
        return run

    async def _advance_waiting_answer(
        self,
        *,
        run: TicketRun,
        task: dict[str, Any],
        comments: list[dict[str, Any]],
        actor: str,
    ) -> TicketRun:
        pc_name, printer_address = self.extract_printer_parameters(task)
        collected = dict((run.trigger_snapshot_json or {}).get("collected_params", {}))
        pc_name = pc_name or str(collected.get("pc_name") or "")
        printer_address = printer_address or str(collected.get("printer_address") or "")
        if (
            run.waiting_reason == "missing_printer_parameters"
            and pc_name
            and printer_address
        ):
            run.waiting_reason = None
            run.waiting_until = None
            return await self._record_step(
                run,
                step="validate_request",
                state=TicketRunState.RUNNING.value,
                actor=actor,
            )

        from app.services.vault import get_service_account_user_id

        service_user_id = await get_service_account_user_id(self.db)
        assistant_id = str(service_user_id) if service_user_id is not None else None
        creator_id = str(task.get("CreatorId")) if task.get("CreatorId") is not None else None
        eligible: list[dict[str, Any]] = []
        for comment in comments:
            raw_editor = comment.get("EditorId") or comment.get("UserId")
            editor = str(raw_editor) if raw_editor is not None else None
            if creator_id is None or editor != creator_id or editor == assistant_id:
                continue
            raw_created = comment.get("Created") or comment.get("Date")
            try:
                created = dt.datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                continue
            waiting_started = (
                run.waiting_until - dt.timedelta(hours=72)
                if run.waiting_until
                else run.updated_at
            )
            if waiting_started and waiting_started.tzinfo is None:
                waiting_started = waiting_started.replace(tzinfo=dt.timezone.utc)
            if waiting_started and created <= waiting_started:
                continue
            eligible.append(comment)

        if eligible:
            latest = max(
                eligible,
                key=lambda item: str(item.get("Created") or item.get("Date") or ""),
            )
            text = str(latest.get("Comment") or latest.get("Comments") or "").strip()
            intent = IntentAnalyzer.analyze_fast_regex(text)
            if intent and intent.intent.value == "cancel_request":
                return await self._create_cancellation(
                    run,
                    step="cancel_not_relevant",
                    template_key="ticket_not_relevant",
                    context={},
                    actor=actor,
                )
            if intent and intent.intent.value == "provide_data":
                if intent.extracted_pc:
                    collected["pc_name"] = intent.extracted_pc
                if intent.extracted_ip:
                    collected["printer_address"] = intent.extracted_ip
                snapshot = dict(run.trigger_snapshot_json or {})
                snapshot["collected_params"] = collected
                run.trigger_snapshot_json = snapshot
                if collected.get("pc_name") and collected.get("printer_address"):
                    run.waiting_reason = None
                    run.waiting_until = None
                    return await self._record_step(
                        run,
                        step="validate_request",
                        state=TicketRunState.RUNNING.value,
                        actor=actor,
                    )
            if run.waiting_reason == "pc_offline":
                run.waiting_reason = None
                run.waiting_until = None
                return await self._record_step(
                    run,
                    step="validate_request",
                    state=TicketRunState.RUNNING.value,
                    actor=actor,
                )
            if run.clarification_count >= 2:
                run.pause_reason = "clarification_unresolved"
                return await self._record_step(
                    run,
                    step="wait_for_answer",
                    state=TicketRunState.PAUSED.value,
                    actor=actor,
                    error_code="clarification_unresolved",
                    error_message="Получен ответ, но обязательные реквизиты не распознаны",
                    event_details={"requires_attention": True},
                )
            return await self._record_step(
                run,
                step="validate_request",
                state=TicketRunState.RUNNING.value,
                actor=actor,
            )

        now = dt.datetime.now(dt.timezone.utc)
        waiting_until = run.waiting_until
        if waiting_until and waiting_until.tzinfo is None:
            waiting_until = waiting_until.replace(tzinfo=dt.timezone.utc)
        if waiting_until and now >= waiting_until:
            return await self._create_cancellation(
                run,
                step="cancel_timeout",
                template_key="ticket_timeout_cancel",
                context={},
                actor=actor,
            )
        return run
