"""Durable sequential scheduler for approved ActionPlans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.contracts import ActionPlan, ActionPlanState
from core.automation.persistence import AutomationRepository
from core.database.models import ActionPlanRecord, CommandRecord


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
    ) -> RunnerAdvanceResult:
        record = await session.scalar(
            select(ActionPlanRecord).where(ActionPlanRecord.id == action_plan_id).with_for_update()
        )
        if record is None:
            raise LookupError("ActionPlan not found")
        plan = ActionPlan.model_validate(record.plan_json)
        if plan.state not in {ActionPlanState.approved, ActionPlanState.running}:
            raise ValueError("ActionPlan must be approved or running")

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

            running_plan = plan.model_copy(update={"state": ActionPlanState.running})
            command = self.repository.command_for_action(
                plan=plan,
                action=action,
                initiator=initiator,
            )
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
