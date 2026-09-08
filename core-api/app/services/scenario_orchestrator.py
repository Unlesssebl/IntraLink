"""Durable event-driven orchestration for typed ticket scenarios."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.domain import (
    ActionProposed,
    CandidateOutcome,
    ClarificationRequired,
    DecisionEnvelope,
    Evidence,
    ManualReviewRequired,
    NoMatch,
    ResolutionProposed,
)

from app.database.db import CommandRecord, TicketRun, TicketRunEvent
from app.services.command_service import CommandService
from app.services.command_delivery import CommandDeliveryService
from app.services.decision_journal import DecisionJournalService
from app.services.facts import TicketFactStore, collect_ticket_observations
from app.services.scenario_decision import ScenarioDecisionService
from app.services.resolution_service import ResolutionUnavailable, resolve_outcome
from app.services.scenarios import get_scenario_registry
from app.services.ticket_runs import TicketRunService, TicketRunState


@dataclass(frozen=True, slots=True)
class OrchestrationResult:
    run: TicketRun
    envelope: DecisionEnvelope | None
    command: CommandRecord | None
    duplicate_event: bool = False


def ticket_event_key(
    task: dict[str, Any], comments: list[dict[str, Any]] | None, event_type: str
) -> str:
    payload = json.dumps(
        {
            "event_type": event_type,
            "task": task,
            "comments": comments or [],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return f"{event_type}:{hashlib.sha256(payload.encode()).hexdigest()[:32]}"


class CommandDispatcher:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def dispatch(
        self,
        *,
        run: TicketRun,
        envelope: DecisionEnvelope,
        action: str,
        target: dict[str, Any],
        parameters: dict[str, Any],
        actor: str,
        task: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> CommandRecord:
        decision = await DecisionJournalService(self.db).record_operational(
            task_id=run.task_id,
            ticket_run_id=run.id,
            action=action,
            target=target,
            parameters=parameters,
            actor=actor,
            task=task,
            history=comments,
        )
        command, _duplicate = await CommandService(self.db).create(
            action=action,
            target=target,
            parameters=parameters,
            idempotency_key=(
                f"{run.id}:{envelope.decision_version}:{action}"
            ),
            initiator=actor,
            source="scenario_orchestrator",
            priority=5,
            ticket_run_id=run.id,
            decision_id=decision.id,
            decision_version=decision.version,
        )
        run.decision_version = decision.version
        return command


class TicketRunOrchestrator:
    """Advance one generic scenario step without holding locks during collection."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.runs = TicketRunService(db)
        self.facts = TicketFactStore(db)
        self.decisions = ScenarioDecisionService(db)
        self.commands = CommandDispatcher(db)

    async def _event_exists(self, run_id: uuid.UUID, event_key: str) -> bool:
        return (
            await self.db.scalar(
                select(TicketRunEvent.id).where(
                    TicketRunEvent.ticket_run_id == run_id,
                    TicketRunEvent.event_key == event_key,
                )
            )
        ) is not None

    async def _latest_command(self, run_id: uuid.UUID) -> CommandRecord | None:
        return await self.db.scalar(
            select(CommandRecord)
            .where(CommandRecord.ticket_run_id == run_id)
            .order_by(CommandRecord.created_at.desc())
            .limit(1)
        )

    async def _reconcile_command(
        self,
        run: TicketRun,
        *,
        actor: str,
        service_auth_b64: str | None,
        task: dict[str, Any],
        comments: list[dict[str, Any]],
    ) -> OrchestrationResult | None:
        command = await self._latest_command(run.id)
        if command is None:
            return None
        event_key = f"command:{command.id}:{command.version}:{command.status}"
        if await self._event_exists(run.id, event_key):
            return None
        if command.status in {"awaiting_approval", "queued", "running"}:
            run.state = (
                TicketRunState.WAITING_APPROVAL.value
                if command.status == "awaiting_approval"
                else TicketRunState.RUNNING.value
            )
            return OrchestrationResult(run, None, command)
        if command.status in {"failed", "needs_review", "rejected", "cancelled"}:
            run.state = TicketRunState.PAUSED.value
            run.pause_reason = f"command_{command.status}"
            run.error_code = run.pause_reason
            run.error_message = command.error_message
        elif command.status == "succeeded" and command.action == "create_user":
            if not service_auth_b64:
                run.state = TicketRunState.PAUSED.value
                run.pause_reason = "verified_result_delivery_requires_auth"
            else:
                await CommandDeliveryService(self.db).deliver_create_user(
                    command.id,
                    actor=actor,
                    service_auth_b64=service_auth_b64,
                )
                return OrchestrationResult(run, None, command)
        elif command.status == "succeeded" and run.current_step == "request_clarification":
            run.state = TicketRunState.WAITING_ANSWER.value
            run.waiting_reason = "clarification_requested"
            run.clarification_count += 1
        elif command.status == "succeeded" and command.action == "apply_triage":
            run.state = TicketRunState.COMPLETED.value
            run.outcome = "completed"
            run.completed_at = dt.datetime.now(dt.timezone.utc)
        elif command.status == "succeeded":
            try:
                scenario = get_scenario_registry().get(
                    run.scenario_key or "", run.scenario_version
                )
                if scenario is None:
                    raise ValueError("pinned_scenario_version_unavailable")
                outcome_key = scenario.definition.success_outcome_key
                if not outcome_key:
                    raise ResolutionUnavailable("scenario_success_outcome_missing")
                policy = await resolve_outcome(
                    self.db,
                    outcome_key,
                    {},
                    expected_kind="resolution",
                )
                outcome = ResolutionProposed(
                    rule_key=f"scenario.{scenario.definition.key}.verified",
                    rule_version=str(scenario.definition.version),
                    outcome_key=outcome_key,
                    target_status_id=policy.get("status_id"),
                    evidence=[
                        Evidence(
                            source="worker",
                            field="command_id",
                            code="verified_success",
                            detail=str(command.id),
                        )
                    ],
                )
                envelope = DecisionEnvelope(
                    decision_version=(run.decision_version or 0) + 1,
                    scenario_key=scenario.definition.key,
                    scenario_version=scenario.definition.version,
                    facts_revision=run.fact_revision,
                    facts_summary={},
                    candidates=[
                        CandidateOutcome(
                            candidate_id=f"verified:{command.id}",
                            source="diagnostic",
                            outcome=outcome,
                            evidence_refs=["worker:command_id:verified_success"],
                            score=1.0,
                            can_authorize_action=False,
                        )
                    ],
                    outcome=outcome,
                    policy=policy,
                    response_draft=policy["comment"],
                    evidence_refs=["worker:command_id:verified_success"],
                    confidence=0.99,
                    requires_approval=bool(policy.get("requires_approval")),
                    status="proposed",
                )
                final_command = await self.commands.dispatch(
                    run=run,
                    envelope=envelope,
                    action="apply_triage",
                    target={"task_id": run.task_id},
                    parameters={
                        "task_ids": [run.task_id],
                        "status_id": policy["status_id"],
                        "comment": policy["comment"],
                        "expenses": policy.get("expenses") or 0,
                    },
                    actor=actor,
                    task=task,
                    comments=comments,
                )
                run.current_step = "finalize:apply_triage"
                run.state = (
                    TicketRunState.WAITING_APPROVAL.value
                    if final_command.status == "awaiting_approval"
                    else TicketRunState.RUNNING.value
                )
                command = final_command
            except (KeyError, ResolutionUnavailable, ValueError) as exc:
                run.state = TicketRunState.PAUSED.value
                run.pause_reason = "verified_action_requires_finalization"
                run.error_message = str(exc)
        else:
            return None

        run.version += 1
        run.updated_by = actor
        await self.runs._append_run_event(
            run,
            event_type="command_reconciled",
            event_key=event_key,
            actor=actor,
            details={
                "command_id": str(command.id),
                "command_status": command.status,
                "command_action": command.action,
                "run_state": run.state,
            },
        )
        await self.db.commit()
        await self.db.refresh(run)
        return OrchestrationResult(run, None, command)

    @staticmethod
    def _action_payload(
        run: TicketRun, envelope: DecisionEnvelope
    ) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
        outcome = envelope.outcome
        policy = envelope.policy or {}
        if isinstance(outcome, ActionProposed):
            parameters = (
                outcome.parameters.model_dump(mode="json")
                if hasattr(outcome.parameters, "model_dump")
                else dict(outcome.parameters)
            )
            return outcome.action, {"task_id": run.task_id}, parameters
        if isinstance(outcome, (ClarificationRequired, ResolutionProposed)):
            status_id = policy.get("status_id")
            if status_id is None:
                return None
            return (
                "apply_triage",
                {"task_id": run.task_id},
                {
                    "task_ids": [run.task_id],
                    "status_id": status_id,
                    "comment": envelope.response_draft,
                    "expenses": policy.get("expenses") or 0,
                },
            )
        return None

    async def observe(
        self,
        *,
        run_id: uuid.UUID,
        task: dict[str, Any],
        comments: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
    ) -> DecisionEnvelope:
        run = await self.db.get(TicketRun, run_id)
        if run is None:
            raise ValueError("run_not_found")
        return await self.decisions.analyze(
            task=task,
            comments=comments,
            diagnostics=diagnostics,
            kb_matches=kb_matches,
            fact_revision=run.fact_revision,
            decision_version=(run.decision_version or 0) + 1,
        )

    async def record_shadow(
        self,
        *,
        run_id: uuid.UUID,
        task: dict[str, Any],
        comments: list[dict[str, Any]] | None = None,
        legacy_scenario_key: str | None = None,
    ) -> DecisionEnvelope:
        envelope = await self.observe(run_id=run_id, task=task, comments=comments)
        run = await self.db.get(TicketRun, run_id)
        if run is None:
            raise ValueError("run_not_found")
        event_key = "shadow:" + ticket_event_key(
            task, comments, "ticket_changed"
        ).split(":", 1)[-1]
        if not await self._event_exists(run.id, event_key):
            await self.runs._append_run_event(
                run,
                event_type="scenario_shadow_compared",
                event_key=event_key,
                actor="scenario_orchestrator:shadow",
                details={
                    "legacy_scenario_key": legacy_scenario_key,
                    "scenario_key": envelope.scenario_key,
                    "scenario_version": envelope.scenario_version,
                    "outcome_kind": envelope.outcome.kind,
                    "outcome_key": getattr(envelope.outcome, "outcome_key", None),
                    "confidence": envelope.confidence,
                },
            )
            await self.db.commit()
        return envelope

    async def advance(
        self,
        *,
        run_id: uuid.UUID,
        task: dict[str, Any],
        comments: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        event_type: str = "ticket_changed",
        event_key: str | None = None,
        actor: str = "scenario_orchestrator",
        service_auth_b64: str | None = None,
    ) -> OrchestrationResult:
        comments = comments or []
        key = event_key or ticket_event_key(task, comments, event_type)
        initial = await self.db.get(TicketRun, run_id)
        if initial is None:
            raise ValueError("run_not_found")
        reconciled = await self._reconcile_command(
            initial,
            actor=actor,
            service_auth_b64=service_auth_b64,
            task=task,
            comments=comments,
        )
        if reconciled is not None:
            return reconciled
        if await self._event_exists(run_id, key):
            return OrchestrationResult(initial, None, None, duplicate_event=True)
        initial_version = initial.version
        initial_fact_revision = initial.fact_revision
        initial_decision_version = initial.decision_version or 0
        pinned_scenario = get_scenario_registry().get(
            initial.scenario_key or "", initial.scenario_version
        )
        # End the read transaction before diagnostics/LLM/network collection.
        # Only immutable scalar snapshots are used until the row is locked again.
        await self.db.rollback()

        # Network/LLM collection happens before the write lock.
        observations = await collect_ticket_observations(
            task, comments=comments, diagnostics=diagnostics
        )
        envelope = await self.decisions.analyze(
            task=task,
            comments=comments,
            diagnostics=diagnostics,
            kb_matches=kb_matches,
            observations=observations,
            fact_revision=initial_fact_revision + 1,
            decision_version=initial_decision_version + 1,
            pinned_scenario_key=(
                pinned_scenario.definition.key if pinned_scenario else None
            ),
            pinned_scenario_version=(
                pinned_scenario.definition.version if pinned_scenario else None
            ),
        )

        run = await self.db.scalar(
            select(TicketRun).where(TicketRun.id == run_id).with_for_update()
        )
        if run is None:
            raise ValueError("run_not_found")
        if run.version != initial_version:
            await self.db.rollback()
            raise ValueError("ticket_run_version_conflict")
        if run.completed_at is not None:
            return OrchestrationResult(run, envelope, None)
        if await self._event_exists(run_id, key):
            return OrchestrationResult(run, None, None, duplicate_event=True)

        await self.facts.append(run.id, observations)
        run.fact_revision += 1
        run.scenario_key = envelope.scenario_key
        run.scenario_version = envelope.scenario_version
        run.context_fingerprint = key.split(":", 1)[-1]
        run.updated_by = actor

        command: CommandRecord | None = None
        payload = self._action_payload(run, envelope)
        if isinstance(envelope.outcome, (ManualReviewRequired, NoMatch)):
            run.state = TicketRunState.PAUSED.value
            run.pause_reason = "manual_review"
            run.current_step = "manual_review"
        elif envelope.policy.get("resolution_error"):
            run.state = TicketRunState.SYSTEM_ERROR.value
            run.error_code = "resolution_unavailable"
            run.error_message = str(envelope.policy["resolution_error"])
            run.current_step = "resolve_policy"
        elif payload is None:
            run.state = TicketRunState.PAUSED.value
            run.pause_reason = "no_executable_resolution"
            run.current_step = "manual_review"
        else:
            action, target, parameters = payload
            command = await self.commands.dispatch(
                run=run,
                envelope=envelope,
                action=action,
                target=target,
                parameters=parameters,
                actor=actor,
                task=task,
                comments=comments,
            )
            if isinstance(envelope.outcome, ClarificationRequired):
                run.current_step = "request_clarification"
            else:
                run.current_step = f"execute:{action}"
            run.state = (
                TicketRunState.WAITING_APPROVAL.value
                if command.status == "awaiting_approval"
                else TicketRunState.RUNNING.value
            )

        run.version += 1
        await self.runs._append_run_event(
            run,
            event_type="scenario_advanced",
            event_key=key,
            actor=actor,
            details={
                "scenario_key": envelope.scenario_key,
                "scenario_version": envelope.scenario_version,
                "fact_revision": run.fact_revision,
                "outcome_kind": envelope.outcome.kind,
                "outcome_key": getattr(envelope.outcome, "outcome_key", None),
                "command_id": str(command.id) if command else None,
                "command_status": command.status if command else None,
            },
        )
        await self.db.commit()
        await self.db.refresh(run)
        return OrchestrationResult(run, envelope, command)
