"""Deterministic first-scenario runner built on Command API v2."""

from __future__ import annotations

import datetime as dt
import logging
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

logger = logging.getLogger("core_api.ticket_run_runner")


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
        service_auth_b64: str | None = None,
    ) -> TicketRun:
        run = await self.db.get(TicketRun, run_id)
        if run is None:
            raise ValueError("run_not_found")
        if run.completed_at is not None or run.mode != "autopilot":
            return run
        if run.state in {TicketRunState.PAUSED.value, TicketRunState.SYSTEM_ERROR.value}:
            return run

        configured_key = run.scenario_key or (run.trigger_snapshot_json or {}).get(
            "scenario_key"
        )
        service_id = int(task.get("ServiceId") or 0)
        from app.services.scenarios.registry import ScenarioRegistry

        norm_key = (
            ScenarioRegistry.normalize_key(configured_key)
            if configured_key
            else configured_key
        )
        scenario_config = await self.db.scalar(
            select(AutopilotScenario).where(
                AutopilotScenario.service_id == service_id,
                AutopilotScenario.scenario_key.in_([configured_key, norm_key]),
                AutopilotScenario.enabled.is_(True),
            )
        )
        rollout_mode = (
            scenario_config.rollout_mode
            if (scenario_config is not None and scenario_config.rollout_mode)
            else "active"
        )
        use_scenario_orchestrator = rollout_mode == "active"
        if rollout_mode == "canary":
            from app.services.scenarios import get_scenario_registry

            percent = int((scenario_config.config_json or {}).get("canary_percent", 10))
            use_scenario_orchestrator = get_scenario_registry().canary_selected(
                run.task_id, percent
            )

        if rollout_mode in {"shadow", "canary"} and not use_scenario_orchestrator:
            try:
                from app.services.scenario_orchestrator import TicketRunOrchestrator

                shadow = await TicketRunOrchestrator(self.db).record_shadow(
                    run_id=run.id,
                    task=task,
                    comments=comments,
                    legacy_scenario_key=configured_key,
                )
                logger.info(
                    "Scenario shadow task=%s legacy=%s scenario=%s outcome=%s confidence=%.3f",
                    run.task_id,
                    configured_key,
                    shadow.scenario_key,
                    shadow.outcome.kind,
                    shadow.confidence,
                )
            except Exception:
                logger.exception("Scenario shadow evaluation failed for task %s", run.task_id)

        if use_scenario_orchestrator:
            from app.services.scenario_orchestrator import TicketRunOrchestrator

            result = await TicketRunOrchestrator(self.db).advance(
                run_id=run.id,
                task=task,
                comments=comments,
                service_auth_b64=service_auth_b64,
                event_type=(
                    "comment_added"
                    if run.state == TicketRunState.WAITING_ANSWER.value
                    else "ticket_changed"
                ),
                actor=actor,
            )
            return result.run

        scenario_key = (run.trigger_snapshot_json or {}).get("scenario_key")
        if scenario_key == "user_creation":
            if run.state == TicketRunState.WAITING_ANSWER.value:
                return await self._advance_user_creation_waiting_answer(
                    run=run,
                    task=task,
                    comments=comments or [],
                    actor=actor,
                    service_auth_b64=service_auth_b64,
                )
            return await self._advance_user_creation(
                run=run,
                task=task,
                comments=comments or [],
                actor=actor,
                service_auth_b64=service_auth_b64,
            )

        if run.state == TicketRunState.WAITING_ANSWER.value:
            return await self._advance_waiting_answer(
                run=run,
                task=task,
                comments=comments or [],
                actor=actor,
                service_auth_b64=service_auth_b64,
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
                    service_auth_b64=service_auth_b64,
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
            err_msg = command.error_message or "Command result requires review"
            if service_auth_b64:
                from app.services.intraservice import add_task_comment

                hidden_msg = (
                    f"🤖 [IntraLink AutoOps | System Diagnostic]\n"
                    f"⚠️ Ошибка выполнения команды: {command.action}\n"
                    f"• Статус: {command.status}\n"
                    f"• Детали: {err_msg}\n"
                    f"• Command ID: {command.id}\n"
                    f"• Действие: Автономный цикл переведен на паузу для проверки инженером."
                )
                try:
                    await add_task_comment(
                        service_auth_b64, run.task_id, hidden_msg, is_private=True
                    )
                except Exception as c_err:
                    logger.warning(
                        "Не удалось отправить скрытый комментарий об ошибке: %s", c_err
                    )
            return await self._record_step(
                run,
                step=step,
                state=TicketRunState.PAUSED.value,
                actor=actor,
                command=command,
                error_code=run.pause_reason,
                error_message=err_msg,
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
        service_auth_b64: str | None = None,
    ) -> TicketRun:
        if service_auth_b64:
            from app.services.worker import get_single_task, get_task_comments

            try:
                fresh_task = await get_single_task(service_auth_b64, run.task_id)
            except Exception:
                fresh_task = None

            if isinstance(fresh_task, dict):
                current_status = int(fresh_task.get("StatusId") or 0)
                if current_status in {
                    settings.STATUS_COMPLETED_ID,
                    settings.STATUS_CANCELLED_ID,
                    settings.STATUS_CLOSED_ID,
                }:
                    return await self.runs.finish_external(
                        run_id=run.id,
                        actor="poller",
                        status_id=current_status,
                    )

                from app.services.vault import get_service_account_user_id

                service_user_id = await get_service_account_user_id(self.db)
                if service_user_id and service_user_id not in task_executor_ids(fresh_task):
                    return await self.runs.pause_automatic(
                        run_id=run.id,
                        actor="poller",
                        reason="assistant_removed",
                    )

                try:
                    fresh_comments = await get_task_comments(service_auth_b64, run.task_id)
                except Exception:
                    fresh_comments = None

                if isinstance(fresh_comments, list) and step.startswith("cancel_timeout"):
                    creator_id = (
                        str(fresh_task.get("CreatorId"))
                        if fresh_task.get("CreatorId") is not None
                        else None
                    )
                    assistant_id_str = str(service_user_id) if service_user_id else None
                    waiting_started = (
                        run.waiting_until - dt.timedelta(hours=72)
                        if run.waiting_until
                        else run.updated_at
                    )
                    if waiting_started and waiting_started.tzinfo is None:
                        waiting_started = waiting_started.replace(tzinfo=dt.timezone.utc)

                    new_applicant_comments = []
                    for c in fresh_comments:
                        raw_editor = c.get("EditorId") or c.get("UserId")
                        editor = str(raw_editor) if raw_editor is not None else None
                        if creator_id and editor != creator_id:
                            continue
                        if editor == assistant_id_str:
                            continue
                        raw_created = c.get("Created") or c.get("Date")
                        try:
                            created = dt.datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
                            if created.tzinfo is None:
                                created = created.replace(tzinfo=dt.timezone.utc)
                            if waiting_started and created > waiting_started:
                                new_applicant_comments.append(c)
                        except (TypeError, ValueError):
                            continue

                    if new_applicant_comments:
                        run.waiting_reason = None
                        run.waiting_until = None
                        await self._record_step(
                            run,
                            step="wait_for_answer",
                            state=TicketRunState.RUNNING.value,
                            actor=actor,
                            event_details={"cancellation_aborted": True, "reason": "applicant_replied"},
                        )
                        return await self._advance_waiting_answer(
                            run=run,
                            task=fresh_task,
                            comments=fresh_comments,
                            actor=actor,
                            service_auth_b64=service_auth_b64,
                        )

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
        service_auth_b64: str | None = None,
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
                    service_auth_b64=service_auth_b64,
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
                service_auth_b64=service_auth_b64,
            )
        return run

    async def _advance_user_creation(
        self,
        *,
        run: TicketRun,
        task: dict[str, Any],
        comments: list[dict[str, Any]],
        actor: str,
        service_auth_b64: str | None = None,
    ) -> TicketRun:
        from app.services.actions.policy import PolicyEngine, PolicyMode
        from app.services.ai_synthesis import synthesize_clarification_comment
        from app.services.command_delivery import CommandDeliveryService
        from app.services.intraservice import add_task_comment, update_task_full
        from app.services.rules.credentials import ActionProposed, CredentialsRule

        step = run.current_step or "validate_request"

        if step == "validate_request":
            rule = CredentialsRule()
            outcome = rule.evaluate_typed(task)

            # 1. Если реквизиты не полные / не валидные (например, "test")
            if not isinstance(outcome, ActionProposed):
                if run.clarification_count >= 2:
                    run.pause_reason = "max_clarifications_exceeded"
                    if service_auth_b64:
                        hidden_msg = (
                            "🤖 [IntraLink AutoOps | System Diagnostic]\n"
                            "⚠️ Превышен лимит попыток уточнения реквизитов (2).\n"
                            "• Действие: Автономный цикл приостановлен для ручной проверки инженером."
                        )
                        await add_task_comment(
                            service_auth_b64, run.task_id, hidden_msg, is_private=True
                        )
                    return await self._record_step(
                        run,
                        step="validate_request",
                        state=TicketRunState.PAUSED.value,
                        actor=actor,
                        error_code="max_clarifications_exceeded",
                        error_message="Превышен лимит попыток уточнения реквизитов для создания УЗ",
                        event_details={"requires_attention": True},
                    )

                run.clarification_count += 1
                missing = getattr(outcome, "missing_fields", None) or []
                invalid = getattr(outcome, "invalid_fields", None) or []
                clarification_text = await synthesize_clarification_comment(
                    task=task,
                    missing_fields=missing,
                    invalid_fields=invalid,
                )

                if service_auth_b64:
                    await update_task_full(
                        auth_b64=service_auth_b64,
                        task_id=run.task_id,
                        status_id=settings.STATUS_WAITING_ID,
                        comment=clarification_text,
                        is_private=False,
                    )

                run.waiting_reason = "missing_person_details"
                run.waiting_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=72)
                return await self._record_step(
                    run,
                    step="validate_request",
                    state=TicketRunState.WAITING_ANSWER.value,
                    actor=actor,
                    event_details={
                        "clarification_text": clarification_text,
                        "attempt": run.clarification_count,
                    },
                )

            # 2. Реквизиты валидны (Happy Path)
            policy = await PolicyEngine().get_action_policy("create_user")
            if policy == PolicyMode.DISABLED:
                run.pause_reason = "policy_disabled"
                if service_auth_b64:
                    hidden_msg = (
                        "🤖 [IntraLink AutoOps | System Diagnostic]\n"
                        "⚠️ Действие create_user отключено в Skills Hub политикой безопасности."
                    )
                    await add_task_comment(
                        service_auth_b64, run.task_id, hidden_msg, is_private=True
                    )
                return await self._record_step(
                    run,
                    step="validate_request",
                    state=TicketRunState.PAUSED.value,
                    actor=actor,
                    error_code="policy_disabled",
                    error_message="Действие create_user отключено политикой безопасности",
                    event_details={"requires_attention": True},
                )

            # Создаем команду create_user
            params = outcome.parameters.model_dump()
            params["task_id"] = run.task_id
            command = await self._create_command(
                run,
                step="execute_create_user",
                action="create_user",
                target={"task_id": run.task_id},
                parameters=params,
                actor=actor,
            )

            # Если политика CONFIRM -> ждем подтверждения оператора
            if policy == PolicyMode.CONFIRM or command.status == "awaiting_approval":
                run.waiting_reason = "operator_approval_required"
                return await self._record_step(
                    run,
                    step="execute_create_user",
                    state=TicketRunState.WAITING_APPROVAL.value,
                    actor=actor,
                    command=command,
                )

            return run

        # Шаг execute_create_user: отслеживаем статус команды
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

        if command.status in {"queued", "running"}:
            return run

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

        if command.status in {"rejected", "cancelled"}:
            run.pause_reason = (
                "approval_rejected" if command.status == "rejected" else "command_cancelled"
            )
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
            err_msg = command.error_message or "Ошибка исполнения команды create_user"
            if service_auth_b64:
                hidden_msg = (
                    f"🤖 [IntraLink AutoOps | System Diagnostic]\n"
                    f"⚠️ Ошибка выполнения команды create_user: {err_msg}\n"
                    f"• Command ID: {command.id}\n"
                    f"• Действие: Автономный цикл переведен на паузу для проверки инженером."
                )
                await add_task_comment(
                    service_auth_b64, run.task_id, hidden_msg, is_private=True
                )
            return await self._record_step(
                run,
                step=step,
                state=TicketRunState.PAUSED.value,
                actor=actor,
                command=command,
                error_code=run.pause_reason,
                error_message=err_msg,
                event_details={"requires_attention": True},
            )

        if command.status == "succeeded":
            try:
                await CommandDeliveryService(self.db).deliver_create_user(
                    command.id,
                    actor=actor,
                    service_auth_b64=service_auth_b64,
                )
                run.state = TicketRunState.COMPLETED.value
                run.completed_at = dt.datetime.now(dt.timezone.utc)
                await self._record_step(
                    run,
                    step=step,
                    state=TicketRunState.COMPLETED.value,
                    actor=actor,
                    command=command,
                )
            except Exception as e:
                logger.exception("Ошибка доставки результатов create_user: %s", e)
                if service_auth_b64:
                    hidden_msg = (
                        f"🤖 [IntraLink AutoOps | System Diagnostic]\n"
                        f"⚠️ Ошибка доставки результатов создания УЗ в IntraService: {e}\n"
                        f"• Command ID: {command.id}"
                    )
                    await add_task_comment(
                        service_auth_b64, run.task_id, hidden_msg, is_private=True
                    )
                run.pause_reason = "delivery_failed"
                await self._record_step(
                    run,
                    step=step,
                    state=TicketRunState.PAUSED.value,
                    actor=actor,
                    command=command,
                    error_code="delivery_failed",
                    error_message=str(e),
                )
            return run

        return run

    async def _advance_user_creation_waiting_answer(
        self,
        *,
        run: TicketRun,
        task: dict[str, Any],
        comments: list[dict[str, Any]],
        actor: str,
        service_auth_b64: str | None = None,
    ) -> TicketRun:
        from app.services.vault import get_service_account_user_id

        service_user_id = await get_service_account_user_id(self.db)
        assistant_id = str(service_user_id) if service_user_id is not None else None

        eligible: list[dict[str, Any]] = []
        for comment in comments:
            raw_editor = comment.get("EditorId") or comment.get("UserId")
            editor = str(raw_editor) if raw_editor is not None else None
            if editor == assistant_id:
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
            incoming_texts = [
                str(c.get("Comment") or c.get("Comments") or "").strip()
                for c in eligible
                if str(c.get("Comment") or c.get("Comments") or "").strip()
            ]
            combined_desc = f"{task.get('Description', '')}\n" + "\n".join(incoming_texts)
            enriched_task = dict(task)
            enriched_task["Description"] = combined_desc

            run.waiting_reason = None
            run.waiting_until = None
            run.state = TicketRunState.RUNNING.value
            run.current_step = "validate_request"
            await self._record_step(
                run,
                step="validate_request",
                state=TicketRunState.RUNNING.value,
                actor=actor,
                event_details={"received_clarification": True},
            )
            return await self._advance_user_creation(
                run=run,
                task=enriched_task,
                comments=comments,
                actor=actor,
                service_auth_b64=service_auth_b64,
            )

        if run.waiting_until:
            now = dt.datetime.now(dt.timezone.utc)
            waiting_until = run.waiting_until
            if waiting_until.tzinfo is None:
                waiting_until = waiting_until.replace(tzinfo=dt.timezone.utc)
            if now >= waiting_until:
                run.pause_reason = "clarification_timeout"
                if service_auth_b64:
                    hidden_msg = (
                        "🤖 [IntraLink AutoOps | System Diagnostic]\n"
                        "⚠️ Истекло время ожидания ответа заявителя (72ч).\n"
                        "• Действие: Цикл переведен на паузу для проверки инженером."
                    )
                    from app.services.intraservice import add_task_comment

                    await add_task_comment(
                        service_auth_b64, run.task_id, hidden_msg, is_private=True
                    )
                return await self._record_step(
                    run,
                    step="wait_for_answer",
                    state=TicketRunState.PAUSED.value,
                    actor=actor,
                    error_code="clarification_timeout",
                    error_message="Истекло время ожидания ответа заявителя",
                    event_details={"requires_attention": True},
                )

        return run

