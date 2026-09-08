"""Deterministic runner facade delegating lifecycle management to TicketRunOrchestrator."""

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
from app.services.scenario_orchestrator import TicketRunOrchestrator
from app.services.template_engine import detect_service_redirect, render_template_strict
from app.services.ticket_runs import TicketRunService, TicketRunState, task_executor_ids

logger = logging.getLogger("core_api.ticket_run_runner")


class TicketRunRunner:
    """Thin facade delegating durable lifecycle execution to TicketRunOrchestrator."""

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
        if run.completed_at is not None:
            return run
        if run.mode != "autopilot":
            has_active_command = (
                await self.db.scalar(
                    select(CommandRecord.id).where(
                        CommandRecord.ticket_run_id == run.id,
                        CommandRecord.status.in_(
                            ["queued", "running", "awaiting_approval", "succeeded", "failed"]
                        ),
                    ).limit(1)
                )
            ) is not None
            if run.state != TicketRunState.RUNNING.value and not has_active_command:
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
        if rollout_mode == "legacy":
            logger.info(
                "Legacy rollout mode is deprecated; executing via TicketRunOrchestrator for task %s",
                run.task_id,
            )
            rollout_mode = "active"

        use_scenario_orchestrator = rollout_mode == "active"
        if rollout_mode == "canary":
            from app.services.scenarios import get_scenario_registry

            percent = int((scenario_config.config_json or {}).get("canary_percent", 10))
            use_scenario_orchestrator = get_scenario_registry().canary_selected(
                run.task_id, percent
            )

        if rollout_mode in {"shadow", "canary"} and not use_scenario_orchestrator:
            try:
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
