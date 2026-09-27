"""Fail-closed technical preflight for ActionPlan capabilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.capabilities import (
    CapabilityPreflight,
    CapabilityRegistry,
    PreflightStatus,
)
from core.automation.contracts import ActionPlan, ActionPlanState, ActionProposal, compute_action_plan_hash
from core.automation.persistence import canonical_params_hash
from core.automation.policy import CapabilityHealthService
from core.database.models import ActionPlanRecord, ActionPreflightRecord


class ActionPreflightService:
    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        ttl_seconds: int = 300,
        health: CapabilityHealthService | None = None,
    ) -> None:
        self.registry = registry
        self.ttl_seconds = ttl_seconds
        self.health = health or CapabilityHealthService()

    async def run_plan(
        self,
        session: AsyncSession,
        *,
        action_plan_id: UUID,
    ) -> list[ActionPreflightRecord]:
        plan_record = await session.scalar(
            select(ActionPlanRecord).where(ActionPlanRecord.id == action_plan_id).with_for_update()
        )
        if plan_record is None:
            raise LookupError("ActionPlan not found")
        plan = ActionPlan.model_validate(plan_record.plan_json)
        if plan.state != ActionPlanState.ready:
            raise ValueError("action_plan_not_ready_for_preflight")
        if compute_action_plan_hash(plan) != plan_record.plan_hash:
            raise ValueError("action_plan_hash_mismatch")

        records: list[ActionPreflightRecord] = []
        for action in sorted(plan.actions, key=lambda item: item.sequence_no):
            result = await self._run_action(session, action)
            record = ActionPreflightRecord(
                action_plan_id=plan.id,
                task_id=plan.task_id,
                action_id=action.id,
                capability_key=action.capability_key,
                snapshot_hash=plan.snapshot_hash,
                plan_hash=plan.plan_hash,
                params_hash=canonical_params_hash(action.params),
                status=result.status.value,
                checks_json=result.checks,
                details_json=result.details,
                error_message=result.error_code,
                expires_at=datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
            )
            session.add(record)
            records.append(record)
        await session.flush()
        return records

    async def _run_action(
        self, session: AsyncSession, action: ActionProposal
    ) -> CapabilityPreflight:
        spec = self.registry.get(action.capability_key)
        if spec is None:
            return CapabilityPreflight(status=PreflightStatus.failed, error_code="unknown_capability")
        missing = self.registry.validate_params(action.capability_key, action.params)
        if missing:
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                details={"missing_params": missing},
                error_code="missing_capability_params",
            )
        if not spec.enabled:
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                checks=["capability_enabled"],
                error_code="capability_disabled",
            )
        try:
            await self.health.require_available(session, capability_key=action.capability_key)
        except ValueError:
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                checks=["capability_health"],
                error_code="capability_circuit_broken",
            )
        executor = self.registry.get_executor(action.capability_key)
        if executor is None:
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                checks=["executor_bound"],
                error_code="capability_executor_unavailable",
            )
        try:
            return await executor.preflight(action.params)
        except Exception as exc:
            return CapabilityPreflight(
                status=PreflightStatus.degraded,
                checks=["executor_preflight"],
                error_code=f"preflight_exception:{type(exc).__name__}",
            )
