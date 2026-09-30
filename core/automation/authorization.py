"""Unified plan authorization service for operator and policy_auto paths (ADR 0006).

Ensures that every ActionPlan undergoes identical fail-closed verification,
atomic locking, preflight freshness checks, and append-only decision auditing
regardless of whether the authorization is triggered by an operator or policy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.capabilities import (
    CapabilityRegistry,
    get_default_capability_registry,
)
from core.automation.contracts import (
    ActionPlan,
    ActionPlanState,
    ActionProposal,
    CaseDecisionState,
    ExtractionMethod,
    compute_action_plan_hash,
)
from core.automation.execution_policy import (
    ExecutionPolicyEvaluator,
)
from core.automation.persistence import canonical_params_hash
from core.automation.runner import WorkflowRunner
from core.automation.workflows import (
    WorkflowRegistry,
    get_default_workflow_registry,
)
from core.database.models import (
    ActionPlanRecord,
    ActionPreflightRecord,
    CapabilityExecutionPolicyRecord,
    CaseDecisionRecord,
    CommandRecord,
    ExecutionPolicyDecisionRecord,
    HumanActionRequestRecord,
    OnboardingFactCorrectionRecord,
    PlanFeedbackRecord,
    WorkflowExecutionModeRecord,
)

AuthorizationKind = Literal["operator", "policy_auto"]


@dataclass(frozen=True)
class AuthorizedPlanResult:
    allowed: bool
    plan: ActionPlan | None = None
    command: CommandRecord | None = None
    decisions: list[ExecutionPolicyDecisionRecord] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    skipped: bool = False
    effective_mode: str = "ASSISTED"
    authorization_kind: AuthorizationKind | None = None


class PlanAuthorizationService:
    """Atomic evaluator and authorizer for ActionPlans."""

    def __init__(
        self,
        *,
        capabilities: CapabilityRegistry | None = None,
        workflows: WorkflowRegistry | None = None,
        evaluator: ExecutionPolicyEvaluator | None = None,
        runner: WorkflowRunner | None = None,
    ) -> None:
        self.capabilities = capabilities or get_default_capability_registry()
        self.workflows = workflows or get_default_workflow_registry()
        self.evaluator = evaluator or ExecutionPolicyEvaluator()
        self.runner = runner or WorkflowRunner()

    async def authorize(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis | None = None,
        *,
        action_plan_id: UUID,
        expected_plan_hash: str,
        expected_snapshot_hash: str,
        kind: AuthorizationKind,
        actor: str,
        current_snapshot_hash: str | None = None,
        force_disabled: bool | None = None,
    ) -> AuthorizedPlanResult:
        """Atomically verify and authorize an ActionPlan in a single transaction."""
        # 1. Lock ActionPlanRecord FOR UPDATE
        plan_record = await session.scalar(
            select(ActionPlanRecord)
            .where(ActionPlanRecord.id == action_plan_id)
            .with_for_update()
        )
        if plan_record is None:
            raise LookupError("action_plan_not_found")
        if plan_record.state != ActionPlanState.ready.value:
            raise ValueError(f"action_plan_not_ready:{plan_record.state}")
        if plan_record.plan_hash != expected_plan_hash:
            raise ValueError("plan_hash_mismatch")
        if plan_record.snapshot_hash != expected_snapshot_hash:
            raise ValueError("snapshot_hash_mismatch")
        if current_snapshot_hash is not None and current_snapshot_hash != plan_record.snapshot_hash:
            raise ValueError("snapshot_stale")

        plan = ActionPlan.model_validate(plan_record.plan_json)
        if not plan.actions:
            raise ValueError("action_plan_empty")
        if compute_action_plan_hash(plan) != plan.plan_hash:
            raise ValueError("action_plan_hash_corrupted")

        # 2. Check workflow registry and definition
        definition = self.workflows.get(plan.workflow_key)
        if definition is None:
            raise ValueError(f"unknown_workflow:{plan.workflow_key}")
        if definition.version != plan.workflow_version:
            raise ValueError("workflow_version_mismatch")

        # 3. Lock capability execution policies and workflow mode policy FOR UPDATE
        capability_keys = {action.capability_key for action in plan.actions}
        for key in sorted(capability_keys):
            await session.scalar(
                select(CapabilityExecutionPolicyRecord)
                .where(CapabilityExecutionPolicyRecord.capability_key == key)
                .with_for_update()
            )

        mode_record = await session.scalar(
            select(WorkflowExecutionModeRecord)
            .where(WorkflowExecutionModeRecord.workflow_key == plan.workflow_key)
            .with_for_update()
        )
        effective_mode = mode_record.desired_mode if mode_record else "ASSISTED"
        workflow_policy_revision = mode_record.revision if mode_record else 1

        # 4. Check policy_auto static & dynamic prerequisites
        if kind == "policy_auto":
            auto_eligibility_reasons: list[str] = []
            if effective_mode != "FULL_AUTO":
                auto_eligibility_reasons.append("workflow_mode_not_auto")
            if not definition.auto_execution_eligible:
                auto_eligibility_reasons.append("workflow_not_auto_eligible")
            for action in plan.actions:
                spec = self.capabilities.get(action.capability_key)
                if not spec or not spec.auto_execution_eligible:
                    auto_eligibility_reasons.append(f"capability_not_auto_eligible:{action.capability_key}")
                if action.requires_approval:
                    auto_eligibility_reasons.append(f"action_requires_approval:{action.id}")

            # Check CaseDecision
            decision_record = await session.scalar(
                select(CaseDecisionRecord).where(CaseDecisionRecord.id == plan.case_decision_id)
            )
            if decision_record is None or decision_record.decision_state != CaseDecisionState.selected.value:
                auto_eligibility_reasons.append("case_decision_not_selected")
            elif decision_record.case_frame_json:
                # LLM should not be the sole basis of assertion for autonomous mutation
                frame_data = decision_record.case_frame_json
                assertions = frame_data.get("assertions", [])
                if any(
                    a.get("extraction_method") == ExtractionMethod.llm.value
                    and a.get("kind") in ("intent", "domain")
                    for a in assertions
                ):
                    auto_eligibility_reasons.append("llm_grounded_decision_not_auto")

            # Check corrections, terminal feedback, previous commands
            existing_cmd = await session.scalar(
                select(CommandRecord.id)
                .where(CommandRecord.action_plan_id == plan.id)
                .limit(1)
            )
            if existing_cmd is not None:
                auto_eligibility_reasons.append("plan_already_has_commands")

            existing_fb = await session.scalar(
                select(PlanFeedbackRecord.id)
                .where(PlanFeedbackRecord.action_plan_id == plan.id)
                .limit(1)
            )
            if existing_fb is not None:
                auto_eligibility_reasons.append("terminal_feedback_present")

            corrections_count = await session.scalar(
                select(OnboardingFactCorrectionRecord.id)
                .where(OnboardingFactCorrectionRecord.task_id == plan.task_id)
                .limit(1)
            )
            if corrections_count is not None:
                auto_eligibility_reasons.append("human_corrections_present")

            hitl_count = await session.scalar(
                select(HumanActionRequestRecord.id)
                .where(HumanActionRequestRecord.action_plan_id == plan.id)
                .limit(1)
            )
            if hitl_count is not None:
                auto_eligibility_reasons.append("human_actions_present")

            if auto_eligibility_reasons:
                return AuthorizedPlanResult(
                    allowed=False,
                    plan=plan,
                    reasons=sorted(auto_eligibility_reasons),
                    effective_mode=effective_mode,
                    authorization_kind=kind,
                )

        # 5. Evaluate execution policy for EVERY action in the plan
        denial_decisions: list[ExecutionPolicyDecisionRecord] = []
        allowed_decisions: list[tuple[ActionProposal, Any, ExecutionPolicyDecisionRecord]] = []
        all_reasons: list[str] = []

        now = datetime.now(UTC)
        for action in sorted(plan.actions, key=lambda it: it.sequence_no):
            # Check preflight presence & freshness before evaluator
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
                )
                .order_by(ActionPreflightRecord.created_at.desc())
                .limit(1)
            )
            preflight_reasons: list[str] = []
            if preflight is None:
                preflight_reasons.append("preflight_missing")
            elif preflight.status not in ("passed", "not_applicable"):
                preflight_reasons.append(f"preflight_{preflight.status}")
            else:
                exp = preflight.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=UTC)
                if exp <= now:
                    preflight_reasons.append("preflight_expired")

            eval_decision = await self.evaluator.evaluate_for_action(
                session=session,
                redis_client=redis_client,
                phase="authorization",
                plan=plan,
                action=action,
                registry=self.capabilities,
                authorization_kind=kind,
                operator_approved=(kind == "operator"),
                force_disabled=force_disabled,
                current_snapshot_hash=current_snapshot_hash or plan.snapshot_hash,
                external_guard_reasons=tuple(preflight_reasons),
            )

            decision_id = uuid.uuid4()
            spec = self.capabilities.get(action.capability_key)
            context_dict = {
                "phase": "authorization",
                "action_id": action.id,
                "capability_key": action.capability_key,
                "authorization_kind": kind,
                "workflow_key": plan.workflow_key,
                "workflow_mode": effective_mode,
                "plan_hash": plan.plan_hash,
                "snapshot_hash": plan.snapshot_hash,
                "params_hash": params_hash,
            }
            decision_rec = ExecutionPolicyDecisionRecord(
                id=decision_id,
                phase="authorization",
                capability_key=action.capability_key,
                action_id=action.id,
                authorization_kind=kind,
                allowed=eval_decision.allowed,
                policy_revision=eval_decision.policy_revision,
                workflow_policy_revision=workflow_policy_revision,
                engine_version=eval_decision.engine_version,
                context_hash=eval_decision.context_hash,
                decision_hash=eval_decision.decision_hash,
                context_json=context_dict,
                reasons_json=eval_decision.reasons,
                ticket_id=plan.task_id,
                action_plan_id=plan.id,
            )

            if not eval_decision.allowed:
                all_reasons.extend(eval_decision.reasons)
                denial_decisions.append(decision_rec)
            else:
                allowed_decisions.append((action, eval_decision, decision_rec))

        # 6. If any action is denied, persist append-only denial decisions and stop
        if denial_decisions:
            for rec in denial_decisions:
                session.add(rec)
            for _, _, rec in allowed_decisions:
                session.add(rec)
            await session.flush()
            return AuthorizedPlanResult(
                allowed=False,
                plan=plan,
                decisions=denial_decisions,
                reasons=sorted(set(all_reasons)),
                effective_mode=effective_mode,
                authorization_kind=kind,
            )

        # 7. All actions allowed: persist all authorization decisions
        persisted_decisions: list[ExecutionPolicyDecisionRecord] = []
        for _, _, dec_rec in allowed_decisions:
            session.add(dec_rec)
            persisted_decisions.append(dec_rec)
        await session.flush()

        # 8. Atomically transition ActionPlan to 'authorized'
        authorized_plan = plan.model_copy(update={"state": ActionPlanState.authorized})
        plan_record.state = ActionPlanState.authorized.value
        if kind == "operator":
            plan_record.approved_by = actor
            plan_record.approved_at = datetime.now(UTC)
        plan_record.plan_json = authorized_plan.model_dump(mode="json")

        if kind == "operator":
            feedback = PlanFeedbackRecord(
                action_plan_id=plan.id,
                task_id=plan.task_id,
                operator_username=actor,
                verdict="approved",
                original_plan_hash=plan.plan_hash,
            )
            session.add(feedback)
        await session.flush()

        # 9. Advance runner to create exactly one command for the authorized plan
        advance_res = await self.runner.advance(
            session,
            action_plan_id=plan.id,
            initiator=actor,
        )

        created_command = advance_res.command
        if created_command is not None:
            # Bind created command ID to its matching action's authorization decision
            for dec in persisted_decisions:
                if dec.action_id == created_command.action_id:
                    dec.command_id = created_command.id
            await session.flush()

        return AuthorizedPlanResult(
            allowed=True,
            plan=advance_res.plan,
            command=created_command,
            decisions=persisted_decisions,
            effective_mode=effective_mode,
            authorization_kind=kind,
        )

    async def try_policy_auto_authorize(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis | None = None,
        *,
        action_plan_id: UUID,
        expected_plan_hash: str,
        expected_snapshot_hash: str,
        current_snapshot_hash: str,
        force_disabled: bool | None = None,
    ) -> AuthorizedPlanResult:
        """Safe autopilot entrypoint: checks FULL_AUTO mode and executes or skips gracefully."""
        plan_rec = await session.get(ActionPlanRecord, action_plan_id)
        if plan_rec is None or plan_rec.state != ActionPlanState.ready.value:
            return AuthorizedPlanResult(allowed=False, skipped=True, reasons=["plan_not_ready"])

        mode_record = await session.get(WorkflowExecutionModeRecord, plan_rec.workflow_key)
        desired_mode = mode_record.desired_mode if mode_record else "ASSISTED"
        if desired_mode != "FULL_AUTO":
            return AuthorizedPlanResult(
                allowed=False,
                skipped=True,
                effective_mode=desired_mode,
                reasons=["mode_is_assisted"],
            )

        return await self.authorize(
            session,
            redis_client=redis_client,
            action_plan_id=action_plan_id,
            expected_plan_hash=expected_plan_hash,
            expected_snapshot_hash=expected_snapshot_hash,
            kind="policy_auto",
            actor="autopilot",
            current_snapshot_hash=current_snapshot_hash,
            force_disabled=force_disabled,
        )
