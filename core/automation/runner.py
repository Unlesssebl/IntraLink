"""Durable sequential scheduler for authorized ActionPlans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.contracts import ActionPlan, ActionPlanState
from core.automation.persistence import AutomationRepository
from core.database.models import (
    ActionPlanRecord,
    CapabilityExecutionPolicyRecord,
    CommandRecord,
    ExecutionPermitRecord,
    ExecutionPolicyDecisionRecord,
    WorkflowExecutionModeRecord,
)


class RunnerState(str, Enum):
    command_ready = "command_ready"
    awaiting_command = "awaiting_command"
    completed = "completed"
    needs_review = "needs_review"


@dataclass(frozen=True)
class RunnerAdvanceResult:
    state: RunnerState
    plan: ActionPlan
    command: CommandRecord | None = None
    reason: str | None = None


class WorkflowRunner:
    """Creates at most one runnable command and advances only after proven success."""

    _ACTIVE_COMMAND_STATES = {"pending", "queued", "running"}
    _SUCCESS_COMMAND_STATES = {"succeeded"}

    def __init__(self, repository: AutomationRepository | None = None) -> None:
        self.repository = repository or AutomationRepository()

    async def advance(
        self,
        session: AsyncSession,
        *,
        action_plan_id: UUID,
        initiator: str,
        authorization_decision_id: UUID | None = None,
        authorization_capability_policy_revision: int | None = None,
        authorization_workflow_policy_revision: int | None = None,
        authorization_decision_hash: str | None = None,
        # Legacy parameter aliases
        approval_decision_id: UUID | None = None,
        approval_policy_revision: int | None = None,
        approval_policy_hash: str | None = None,
    ) -> RunnerAdvanceResult:
        effective_auth_decision_id = authorization_decision_id or approval_decision_id
        effective_auth_cap_rev = (
            authorization_capability_policy_revision or approval_policy_revision
        )
        effective_auth_wf_rev = authorization_workflow_policy_revision
        effective_auth_dec_hash = authorization_decision_hash or approval_policy_hash

        record = await session.scalar(
            select(ActionPlanRecord).where(ActionPlanRecord.id == action_plan_id).with_for_update()
        )
        if record is None:
            raise LookupError("ActionPlan not found")
        plan = ActionPlan.model_validate(record.plan_json)
        if plan.state not in {
            ActionPlanState.authorized,
            ActionPlanState.approved,
            ActionPlanState.running,
        }:
            raise ValueError("ActionPlan must be authorized or running")

        commands = list(
            (
                await session.scalars(
                    select(CommandRecord)
                    .where(CommandRecord.action_plan_id == action_plan_id)
                    .order_by(CommandRecord.sequence_no.asc(), CommandRecord.created_at.asc())
                )
            ).all()
        )
        by_action: dict[str, CommandRecord] = {}
        for command in commands:
            if command.action_id is None:
                return await self._needs_review(record, plan, "command_without_action_binding")
            if command.action_id in by_action:
                return await self._needs_review(record, plan, "duplicate_action_command")
            by_action[command.action_id] = command

        action_ids = {action.id for action in plan.actions}
        if not set(by_action).issubset(action_ids):
            return await self._needs_review(record, plan, "foreign_action_command")

        for action in sorted(plan.actions, key=lambda item: item.sequence_no):
            command = by_action.get(action.id)
            if command is not None:
                if command.status in self._ACTIVE_COMMAND_STATES:
                    return RunnerAdvanceResult(RunnerState.awaiting_command, plan, command)
                if command.status not in self._SUCCESS_COMMAND_STATES:
                    return await self._needs_review(record, plan, f"command_{command.status}", command)
                continue

            if any(
                dependency not in by_action
                or by_action[dependency].status not in self._SUCCESS_COMMAND_STATES
                for dependency in action.depends_on_action_ids
            ):
                return await self._needs_review(record, plan, "dependency_not_succeeded")

            permit = None
            if record.autonomy_run_id is not None:
                permit = await session.scalar(
                    select(ExecutionPermitRecord).where(
                        ExecutionPermitRecord.run_id == record.autonomy_run_id,
                        ExecutionPermitRecord.action_plan_id == plan.id,
                        ExecutionPermitRecord.action_id == action.id,
                        ExecutionPermitRecord.capability_key == action.capability_key,
                        ExecutionPermitRecord.snapshot_hash == plan.snapshot_hash,
                        ExecutionPermitRecord.plan_hash == plan.plan_hash,
                        ExecutionPermitRecord.expires_at > datetime.now(UTC),
                        ExecutionPermitRecord.consumed_at.is_(None),
                    )
                )
                if permit is None:
                    return await self._needs_review(record, plan, "execution_permit_missing")
                cmd_auth_dec_id = None
                cmd_auth_cap_rev = None
                cmd_auth_wf_rev = None
                cmd_auth_dec_hash = None
            else:
                # Compatibility path for commands created before the autonomy cutover.
                decision_rec = await session.scalar(
                    select(ExecutionPolicyDecisionRecord)
                    .where(
                        ExecutionPolicyDecisionRecord.action_plan_id == plan.id,
                        ExecutionPolicyDecisionRecord.action_id == action.id,
                        ExecutionPolicyDecisionRecord.phase.in_(["authorization", "approval"]),
                    )
                    .order_by(ExecutionPolicyDecisionRecord.created_at.desc())
                    .limit(1)
                )

            if record.autonomy_run_id is None and decision_rec is not None:
                if not decision_rec.allowed:
                    return await self._needs_review(record, plan, "authorization_decision_denied")
                if decision_rec.capability_key != action.capability_key:
                    return await self._needs_review(record, plan, "decision_capability_mismatch")

                # Verify capability policy revision has not been tightened
                cap_policy = await session.get(CapabilityExecutionPolicyRecord, action.capability_key)
                if (
                    cap_policy is not None
                    and decision_rec.policy_revision is not None
                    and cap_policy.revision != decision_rec.policy_revision
                ):
                    return await self._needs_review(record, plan, "capability_policy_revision_mismatch")

                # Verify workflow execution mode revision has not changed
                wf_mode = await session.get(WorkflowExecutionModeRecord, plan.workflow_key)
                if (
                    wf_mode is not None
                    and decision_rec.workflow_policy_revision is not None
                    and wf_mode.revision != decision_rec.workflow_policy_revision
                ):
                    return await self._needs_review(record, plan, "workflow_policy_revision_mismatch")

                cmd_auth_dec_id = decision_rec.id
                cmd_auth_cap_rev = decision_rec.policy_revision
                cmd_auth_wf_rev = decision_rec.workflow_policy_revision
                cmd_auth_dec_hash = decision_rec.decision_hash
            elif record.autonomy_run_id is None and effective_auth_decision_id is not None:
                cmd_auth_dec_id = effective_auth_decision_id
                cmd_auth_cap_rev = effective_auth_cap_rev
                cmd_auth_wf_rev = effective_auth_wf_rev
                cmd_auth_dec_hash = effective_auth_dec_hash
            elif record.autonomy_run_id is None and plan.state == ActionPlanState.authorized:
                # Strictly fail-closed for authorized plans without decisions
                return await self._needs_review(record, plan, "authorization_decision_missing")
            else:
                # Legacy approved plans in tests without persisted decisions
                cmd_auth_dec_id = None
                cmd_auth_cap_rev = None
                cmd_auth_wf_rev = None
                cmd_auth_dec_hash = None

            running_plan = plan.model_copy(update={"state": ActionPlanState.running})
            command = self.repository.command_for_action(
                plan=plan,
                action=action,
                initiator=initiator,
                authorization_decision_id=cmd_auth_dec_id,
                authorization_capability_policy_revision=cmd_auth_cap_rev,
                authorization_workflow_policy_revision=cmd_auth_wf_rev,
                authorization_decision_hash=cmd_auth_dec_hash,
            )
            if permit is not None:
                command.autonomy_run_id = record.autonomy_run_id
                command.execution_permit_id = permit.id
            session.add(command)
            record.state = ActionPlanState.running.value
            record.plan_json = running_plan.model_dump(mode="json")
            await session.flush()
            return RunnerAdvanceResult(RunnerState.command_ready, running_plan, command)

        completed = plan.model_copy(update={"state": ActionPlanState.succeeded})
        record.state = ActionPlanState.succeeded.value
        record.plan_json = completed.model_dump(mode="json")
        await session.flush()
        return RunnerAdvanceResult(RunnerState.completed, completed)

    @staticmethod
    async def _needs_review(
        record: ActionPlanRecord,
        plan: ActionPlan,
        reason: str,
        command: CommandRecord | None = None,
    ) -> RunnerAdvanceResult:
        stopped = plan.model_copy(update={"state": ActionPlanState.needs_review})
        record.state = ActionPlanState.needs_review.value
        record.plan_json = stopped.model_dump(mode="json")
        return RunnerAdvanceResult(RunnerState.needs_review, stopped, command, reason)
