"""Persistence boundary for the ADR 0006 automation aggregate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.contracts import (
    ActionPlan,
    ActionPlanState,
    ActionProposal,
    CaseDecision,
    CaseFrame,
    WorkflowPlan,
    compute_action_plan_hash,
)
from core.automation.service_routing import RedirectPlan, ServiceCompatibilityDecision
from core.database.models import (
    ActionPlanRecord,
    ActionPreflightRecord,
    CaseDecisionRecord,
    CommandRecord,
    RedirectPlanRecord,
    ServiceCompatibilityDecisionRecord,
    WorkflowPlanRecord,
    sanitize_secrets,
)


@dataclass(frozen=True)
class AutomationBundle:
    frame: CaseFrame
    decision: CaseDecision
    compatibility: ServiceCompatibilityDecision
    workflow_plan: WorkflowPlan
    action_plan: ActionPlan | None
    redirect_plan: RedirectPlan | None


def canonical_params_hash(params: dict[str, Any]) -> str:
    encoded = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _secret_free(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_secrets(payload)
    if sanitized != payload:
        raise ValueError("Automation records must not contain secrets")
    return payload


class AutomationRepository:
    """Stores and reloads one immutable analysis generation per snapshot."""

    async def save_analysis(
        self,
        session: AsyncSession,
        *,
        frame: CaseFrame,
        decision: CaseDecision,
        workflow_plan: WorkflowPlan,
        action_plan: ActionPlan | None,
        compatibility: ServiceCompatibilityDecision | None = None,
        redirect_plan: RedirectPlan | None = None,
    ) -> AutomationBundle:
        compatibility = compatibility or ServiceCompatibilityDecision(
            task_id=decision.task_id,
            snapshot_hash=decision.snapshot_hash,
            case_decision_id=decision.id,
            state="degraded",
            degraded_component="service_catalog",
            reason_codes=["catalog_unavailable"],
        )
        self._validate_bindings(frame, decision, compatibility, workflow_plan, action_plan, redirect_plan)
        frame_json = _secret_free(frame.model_dump(mode="json"))
        decision_json = _secret_free(decision.model_dump(mode="json"))
        workflow_json = _secret_free(workflow_plan.model_dump(mode="json"))

        session.add(
            CaseDecisionRecord(
                id=decision.id,
                task_id=decision.task_id,
                snapshot_hash=decision.snapshot_hash,
                frame_id=frame.id,
                frame_version=frame.frame_version,
                router_version=decision.router_version,
                prompt_version=decision.prompt_version,
                state=decision.state.value,
                primary_case_type=decision.primary_case_type,
                case_frame_json=frame_json,
                decision_json=decision_json,
            )
        )
        session.add(
            ServiceCompatibilityDecisionRecord(
                id=compatibility.id,
                task_id=compatibility.task_id,
                snapshot_hash=compatibility.snapshot_hash,
                case_decision_id=decision.id,
                source_service_id=compatibility.source_service_id,
                binding_key=compatibility.binding_key,
                binding_version=compatibility.binding_version,
                catalog_hash=compatibility.catalog_hash,
                state=compatibility.state.value,
                decision_json=_secret_free(compatibility.model_dump(mode="json")),
            )
        )
        session.add(
            WorkflowPlanRecord(
                id=workflow_plan.id,
                case_decision_id=decision.id,
                task_id=workflow_plan.task_id,
                snapshot_hash=workflow_plan.snapshot_hash,
                workflow_key=workflow_plan.workflow_key,
                workflow_version=workflow_plan.workflow_version,
                state=workflow_plan.state.value,
                disposition=workflow_plan.disposition.value,
                clarification_round=workflow_plan.clarification_round,
                plan_json=workflow_json,
            )
        )
        if action_plan is not None:
            plan_json = _secret_free(action_plan.model_dump(mode="json"))
            session.add(
                ActionPlanRecord(
                    id=action_plan.id,
                    workflow_plan_id=workflow_plan.id,
                    case_decision_id=decision.id,
                    task_id=action_plan.task_id,
                    snapshot_hash=action_plan.snapshot_hash,
                    workflow_key=action_plan.workflow_key,
                    workflow_version=action_plan.workflow_version,
                    source_service_id=action_plan.source_service_id,
                    service_binding_key=action_plan.service_binding_key,
                    service_binding_version=action_plan.service_binding_version,
                    catalog_hash=action_plan.catalog_hash,
                    state=action_plan.state.value,
                    disposition=action_plan.disposition.value,
                    plan_hash=action_plan.plan_hash,
                    plan_json=plan_json,
                )
            )
        if redirect_plan is not None:
            session.add(
                RedirectPlanRecord(
                    id=redirect_plan.id,
                    task_id=redirect_plan.task_id,
                    snapshot_hash=redirect_plan.snapshot_hash,
                    case_decision_id=redirect_plan.case_decision_id,
                    compatibility_decision_id=redirect_plan.compatibility_decision_id,
                    source_service_id=redirect_plan.source_service_id,
                    target_service_id=redirect_plan.target_service_id,
                    target_service_path=redirect_plan.target_service_path,
                    strategy=redirect_plan.strategy.value,
                    template_key=redirect_plan.template_key,
                    rendered_public_comment=redirect_plan.rendered_public_comment,
                    catalog_hash=redirect_plan.catalog_hash,
                    binding_key=redirect_plan.binding_key,
                    binding_version=redirect_plan.binding_version,
                    plan_hash=redirect_plan.plan_hash,
                    state=redirect_plan.state.value,
                    approval_state=redirect_plan.approval_state,
                    execution_state=redirect_plan.execution_state,
                    version=redirect_plan.version,
                    plan_json=_secret_free(redirect_plan.model_dump(mode="json")),
                )
            )
        await session.flush()
        return AutomationBundle(frame, decision, compatibility, workflow_plan, action_plan, redirect_plan)

    async def latest_for_task(self, session: AsyncSession, task_id: int) -> AutomationBundle | None:
        decision_record = await session.scalar(
            select(CaseDecisionRecord)
            .where(CaseDecisionRecord.task_id == task_id)
            .order_by(CaseDecisionRecord.created_at.desc(), CaseDecisionRecord.id.desc())
            .limit(1)
        )
        if decision_record is None:
            return None
        workflow_record = await session.scalar(
            select(WorkflowPlanRecord)
            .where(WorkflowPlanRecord.case_decision_id == decision_record.id)
            .order_by(WorkflowPlanRecord.created_at.desc(), WorkflowPlanRecord.id.desc())
            .limit(1)
        )
        if workflow_record is None:
            raise RuntimeError("CaseDecision exists without a WorkflowPlan")
        compatibility_record = await session.scalar(
            select(ServiceCompatibilityDecisionRecord)
            .where(ServiceCompatibilityDecisionRecord.case_decision_id == decision_record.id)
            .limit(1)
        )
        if compatibility_record is None:
            raise RuntimeError("CaseDecision exists without a ServiceCompatibilityDecision")
        action_record = await session.scalar(
            select(ActionPlanRecord)
            .where(
                ActionPlanRecord.workflow_plan_id == workflow_record.id,
                ActionPlanRecord.state != ActionPlanState.cancelled.value,
            )
            .order_by(ActionPlanRecord.created_at.desc(), ActionPlanRecord.id.desc())
            .limit(1)
        )
        redirect_record = await session.scalar(
            select(RedirectPlanRecord)
            .where(
                RedirectPlanRecord.compatibility_decision_id == compatibility_record.id,
                RedirectPlanRecord.state != "rejected",
            )
            .order_by(RedirectPlanRecord.created_at.desc())
            .limit(1)
        )
        frame = CaseFrame.model_validate(decision_record.case_frame_json)
        decision = CaseDecision.model_validate(decision_record.decision_json)
        compatibility = ServiceCompatibilityDecision.model_validate(compatibility_record.decision_json)
        workflow = WorkflowPlan.model_validate(workflow_record.plan_json)
        action = ActionPlan.model_validate(action_record.plan_json) if action_record else None
        redirect = RedirectPlan.model_validate(redirect_record.plan_json) if redirect_record else None
        self._validate_bindings(frame, decision, compatibility, workflow, action, redirect)
        return AutomationBundle(frame, decision, compatibility, workflow, action, redirect)

    async def save_action_plan(self, session: AsyncSession, plan: ActionPlan) -> None:
        """Persist an operator-corrected plan after all workflow constraints were compiled."""
        if plan.state != ActionPlanState.ready:
            raise ValueError("corrected_action_plan_not_ready")
        if compute_action_plan_hash(plan) != plan.plan_hash:
            raise ValueError("action_plan_hash_mismatch")
        plan_json = _secret_free(plan.model_dump(mode="json"))
        session.add(
            ActionPlanRecord(
                id=plan.id,
                workflow_plan_id=plan.workflow_plan_id,
                case_decision_id=plan.case_decision_id,
                task_id=plan.task_id,
                snapshot_hash=plan.snapshot_hash,
                workflow_key=plan.workflow_key,
                workflow_version=plan.workflow_version,
                source_service_id=plan.source_service_id,
                service_binding_key=plan.service_binding_key,
                service_binding_version=plan.service_binding_version,
                catalog_hash=plan.catalog_hash,
                state=plan.state.value,
                disposition=plan.disposition.value,
                plan_hash=plan.plan_hash,
                plan_json=plan_json,
            )
        )
        await session.flush()

    async def approve_action_plan(
        self,
        session: AsyncSession,
        *,
        action_plan_id: UUID,
        expected_plan_hash: str,
        expected_snapshot_hash: str,
        operator_username: str,
    ) -> ActionPlan:
        record = await session.scalar(
            select(ActionPlanRecord).where(ActionPlanRecord.id == action_plan_id).with_for_update()
        )
        if record is None:
            raise LookupError("ActionPlan not found")
        plan = ActionPlan.model_validate(record.plan_json)
        if record.plan_hash != expected_plan_hash or compute_action_plan_hash(plan) != expected_plan_hash:
            raise ValueError("stale_or_tampered_action_plan")
        if record.snapshot_hash != expected_snapshot_hash:
            raise ValueError("stale_ticket_snapshot")
        if record.state != ActionPlanState.ready.value:
            raise ValueError("action_plan_not_ready")
        now = datetime.now(UTC)
        for action in plan.actions:
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
                raise ValueError(f"action_preflight_not_passed:{action.id}")
        approved = plan.model_copy(update={"state": ActionPlanState.approved})
        record.state = ActionPlanState.approved.value
        record.approved_by = operator_username
        record.approved_at = datetime.now(UTC)
        record.plan_json = approved.model_dump(mode="json")
        await session.flush()
        return approved

    @staticmethod
    def command_for_action(
        *,
        plan: ActionPlan,
        action: ActionProposal,
        initiator: str,
        executor: str = "worker",
    ) -> CommandRecord:
        if plan.state not in {ActionPlanState.approved, ActionPlanState.running}:
            raise ValueError("Only an approved or running ActionPlan can create commands")
        if compute_action_plan_hash(plan) != plan.plan_hash:
            raise ValueError("ActionPlan canonical hash mismatch")
        if action not in plan.actions:
            raise ValueError("Action does not belong to ActionPlan")
        params_hash = canonical_params_hash(action.params)
        return CommandRecord(
            idempotency_key=f"{plan.id}:{action.id}:{params_hash}",
            action=action.capability_key,
            executor=executor,
            target_json={"task_id": plan.task_id},
            params_json=_secret_free(action.params),
            initiator=initiator,
            task_id=plan.task_id,
            action_plan_id=plan.id,
            action_id=action.id,
            capability_key=action.capability_key,
            sequence_no=action.sequence_no,
            params_hash=params_hash,
            plan_hash=plan.plan_hash,
            snapshot_hash=plan.snapshot_hash,
        )

    @staticmethod
    def _validate_bindings(
        frame: CaseFrame,
        decision: CaseDecision,
        compatibility: ServiceCompatibilityDecision,
        workflow_plan: WorkflowPlan,
        action_plan: ActionPlan | None,
        redirect_plan: RedirectPlan | None,
    ) -> None:
        identity = {(frame.task_id, frame.snapshot_hash), (decision.task_id, decision.snapshot_hash)}
        identity.add((workflow_plan.task_id, workflow_plan.snapshot_hash))
        identity.add((compatibility.task_id, compatibility.snapshot_hash))
        if action_plan is not None:
            identity.add((action_plan.task_id, action_plan.snapshot_hash))
        if len(identity) != 1:
            raise ValueError("Automation aggregate has inconsistent task or snapshot bindings")
        if decision.frame_id != frame.id:
            raise ValueError("CaseDecision is not bound to CaseFrame")
        if workflow_plan.case_decision_id != decision.id:
            raise ValueError("WorkflowPlan is not bound to CaseDecision")
        if compatibility.case_decision_id != decision.id:
            raise ValueError("ServiceCompatibilityDecision is not bound to CaseDecision")
        if action_plan is not None and redirect_plan is not None:
            raise ValueError("ActionPlan and RedirectPlan are mutually exclusive")
        if action_plan is not None:
            if action_plan.case_decision_id != decision.id or action_plan.workflow_plan_id != workflow_plan.id:
                raise ValueError("ActionPlan is not bound to its decision and workflow")
            if compute_action_plan_hash(action_plan) != action_plan.plan_hash:
                raise ValueError("ActionPlan canonical hash mismatch")
        if redirect_plan is not None:
            if (
                redirect_plan.case_decision_id != decision.id
                or redirect_plan.compatibility_decision_id != compatibility.id
            ):
                raise ValueError("RedirectPlan is not bound to its decisions")
