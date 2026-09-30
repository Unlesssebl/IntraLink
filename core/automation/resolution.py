"""Generic HumanActionResolutionService orchestrating the HITL resolution saga."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.capabilities import CapabilityRegistry
from core.automation.contracts import ActionPlan, ActionPlanState, compute_action_plan_hash
from core.automation.persistence import AutomationRepository
from core.automation.preflight import ActionPreflightService
from core.database.models import ActionPlanRecord, HumanActionRequestRecord


class HumanActionResolutionError(Exception):
    """Base exception for human action resolution errors."""


class HumanActionNotFoundError(HumanActionResolutionError):
    """Raised when the requested action record or plan does not exist."""


class HumanActionConflictError(HumanActionResolutionError):
    """Raised when action is already resolved, expired, or hash mismatch occurs."""


class HumanActionValidationError(HumanActionResolutionError):
    """Raised when selected candidate is invalid or rejected by executor validation."""


class HumanActionResolutionService:
    """Orchestrates human action resolution, plan mutation, and preflight re-run."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        repository: AutomationRepository,
        preflight: ActionPreflightService,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.preflight = preflight

    async def resolve(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        human_action_id: UUID,
        candidate_id: str,
        operator: str,
        generation: int | None = None,
        snapshot_hash: str | None = None,
        plan_hash: str | None = None,
    ) -> ActionPlan:
        # 1. Pessimistic lock on HumanActionRequestRecord (EC-3)
        record = await session.scalar(
            select(HumanActionRequestRecord)
            .where(
                HumanActionRequestRecord.id == human_action_id,
                HumanActionRequestRecord.task_id == ticket_id,
            )
            .with_for_update()
        )
        if record is None:
            raise HumanActionNotFoundError("human_action_not_found")
        if record.state != "pending":
            raise HumanActionConflictError("human_action_already_resolved")
        if generation is not None and generation != record.generation:
            raise HumanActionConflictError("human_action_generation_mismatch")
        if snapshot_hash is not None and snapshot_hash != record.snapshot_hash:
            raise HumanActionConflictError("human_action_snapshot_hash_mismatch")
        if record.expires_at is not None:
            expires_at = record.expires_at if record.expires_at.tzinfo is not None else record.expires_at.replace(tzinfo=UTC)
            if expires_at < datetime.now(UTC):
                raise HumanActionConflictError("human_action_expired")

        # 2. Extract selected candidate
        candidates = list(record.candidates_json or [])
        matched = next((c for c in candidates if c.get("candidate_id") == candidate_id), None)
        if matched is None:
            raise HumanActionValidationError("invalid_candidate_id")

        selected_value = matched.get("server_value") or matched.get("distinguished_name")
        if not selected_value:
            raise HumanActionValidationError("candidate_server_value_missing")

        # 3. Polymorphic validation via capability executor (EC-2)
        bundle = await self.repository.latest_for_task(session, ticket_id)
        if bundle is None or bundle.action_plan is None:
            raise HumanActionNotFoundError("automation_bundle_or_plan_missing")

        target_action = next((a for a in bundle.action_plan.actions if a.id == record.action_id), None)
        if target_action is None:
            raise HumanActionNotFoundError("target_action_not_found_in_plan")

        executor = self.registry.get_executor(target_action.capability_key)
        if executor is not None:
            validate_fn = getattr(executor, "validate_human_action", None)
            if callable(validate_fn):
                is_valid = await validate_fn(record.target_param, selected_value)
                if not is_valid:
                    raise HumanActionValidationError("candidate_validation_failed_by_executor")

        # 4. Mark record as resolved
        record.selected_candidate_id = candidate_id
        record.state = "resolved"
        record.resolved_by = operator
        record.resolved_at = datetime.now(UTC)

        # 5. Mutate only the target action in ActionPlan (EC-6)
        old_plan_rec = await session.scalar(
            select(ActionPlanRecord).where(ActionPlanRecord.id == bundle.action_plan.id).with_for_update()
        )
        updated_actions = []
        for act in bundle.action_plan.actions:
            if act.id == record.action_id:
                new_params = dict(act.params)
                new_params[record.target_param] = selected_value
                updated_actions.append(act.model_copy(update={"params": new_params}))
            else:
                updated_actions.append(act)

        draft = bundle.action_plan.model_copy(
            update={
                "id": uuid.uuid4(),
                "actions": updated_actions,
                "state": ActionPlanState.ready,
            }
        )
        new_plan_hash = compute_action_plan_hash(draft)
        corrected_plan = draft.model_copy(update={"plan_hash": new_plan_hash})

        if old_plan_rec is not None:
            old_plan_rec.state = ActionPlanState.cancelled.value

        try:
            await self.repository.save_action_plan(session, corrected_plan)
        except ValueError as exc:
            raise HumanActionValidationError(str(exc)) from exc

        # 6. Re-run preflight on the corrected plan
        await self.preflight.run_plan(session, action_plan_id=corrected_plan.id)
        return corrected_plan
