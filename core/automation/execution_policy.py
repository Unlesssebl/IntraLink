"""Canonical execution policy engine and decision evaluator for ADR 0006.

Provides a unified, domain-agnostic evaluation gate for both 'approval'
and 'execution' phases across API and worker components.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import redis.asyncio as aioredis
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from core.automation.capabilities import CapabilityRegistry
    from core.automation.contracts import ActionPlan, ActionProposal

POLICY_ENGINE_VERSION = "2.0.0"

ExecutionPhase = Literal["approval", "execution"]


class ExecutionPolicyDecision(BaseModel):
    """Immutable, auditable evaluation decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    phase: str
    capability_key: str
    policy_revision: int | None = None
    engine_version: str = POLICY_ENGINE_VERSION
    context_hash: str
    decision_hash: str
    reasons: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ExecutionPolicyContext:
    """Full operational context consumed by ExecutionPolicyEvaluator."""

    phase: str
    capability_key: str
    is_mutating: bool
    requires_binding: bool = False

    # 1. Static capability catalog & executor
    capability_registered: bool = False
    executor_bound: bool = False

    # 2. Authorization & service route binding
    binding_active: bool = False
    binding_validated: bool = False

    # 3. Administrative policy state from database
    policy_present: bool = False
    desired_state: str = "disabled"
    policy_revision: int | None = None

    # 4. Host/node-local deployment ceiling
    force_disabled: bool = True

    # 5. Technical health / circuit breaker
    circuit_broken: bool = False

    # 6. Worker attestation
    worker_available: bool = False
    worker_compatible: bool = False
    worker_force_disabled: bool = False

    # 7. ActionPlan, snapshot, params and preflight integrity
    plan_valid: bool = False
    snapshot_current: bool = False
    params_valid: bool = False
    preflight_passed: bool = False
    preflight_fresh: bool = False

    # 8. Execution-phase only operator approval invariants
    operator_approved: bool = False
    approval_decision_id: UUID | None = None
    approval_policy_revision: int | None = None
    approval_policy_hash: str | None = None

    def to_hash_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.approval_decision_id is not None:
            data["approval_decision_id"] = str(self.approval_decision_id)
        return data


def compute_context_hash(context_dict: dict[str, Any]) -> str:
    canonical_repr = json.dumps(context_dict, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def compute_decision_hash(
    *,
    allowed: bool,
    phase: str,
    capability_key: str,
    policy_revision: int | None,
    engine_version: str,
    context_hash: str,
    reasons: list[str],
) -> str:
    payload = {
        "allowed": allowed,
        "capability_key": capability_key,
        "context_hash": context_hash,
        "engine_version": engine_version,
        "phase": phase,
        "policy_revision": policy_revision,
        "reasons": sorted(reasons),
    }
    canonical_repr = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def is_deployment_force_disabled() -> bool:
    """Read host-local deployment ceiling; defaults to fail-closed True."""
    raw = os.getenv("MUTATING_AUTOMATION_FORCE_DISABLED", "true").strip().lower()
    return raw in ("true", "1", "yes", "")


class ExecutionPolicyEvaluator:
    """Canonical evaluator: pure conjunction of all safety, administrative and technical gates."""

    def __init__(self, engine_version: str = POLICY_ENGINE_VERSION) -> None:
        self.engine_version = engine_version

    def evaluate(self, ctx: ExecutionPolicyContext) -> ExecutionPolicyDecision:
        reasons: list[str] = []

        # 1. Capability registry & executor binding
        if not ctx.capability_registered:
            reasons.append("capability_not_registered")
        if not ctx.executor_bound:
            reasons.append("executor_missing")

        # 2. Circuit breaker check (technical health)
        if ctx.circuit_broken:
            reasons.append("circuit_broken")

        # 3. Worker attestation
        if not ctx.worker_available:
            reasons.append("worker_unavailable")
        elif not ctx.worker_compatible:
            reasons.append("worker_incompatible")
        elif ctx.worker_force_disabled and ctx.is_mutating:
            reasons.append("worker_force_disabled")

        # 4. ActionPlan, snapshot, params and preflight integrity
        if not ctx.plan_valid:
            reasons.append("plan_invalid")
        if not ctx.snapshot_current:
            reasons.append("snapshot_stale")
        if not ctx.params_valid:
            reasons.append("params_mismatch")
        if not ctx.preflight_passed:
            reasons.append("preflight_not_passed")
        elif not ctx.preflight_fresh:
            reasons.append("preflight_expired")

        # Mutating capability gates
        if ctx.is_mutating:
            # Authorization scope / service route binding
            if ctx.requires_binding:
                if not ctx.binding_active:
                    reasons.append("binding_not_active")
                if not ctx.binding_validated:
                    reasons.append("binding_not_validated")

            # Administrative policy state from database
            if not ctx.policy_present:
                reasons.append("policy_not_found")
            elif ctx.desired_state != "enabled":
                reasons.append("policy_disabled")

            # Host-local deployment ceiling
            if ctx.force_disabled:
                reasons.append("deployment_force_disabled")

        # Execution phase: mandatory operator approval and strict approval binding
        if ctx.phase == "execution":
            if not ctx.operator_approved:
                reasons.append("operator_approval_missing")
            if ctx.approval_decision_id is None:
                reasons.append("approval_decision_missing")
            if ctx.approval_policy_revision is None or ctx.approval_policy_revision != ctx.policy_revision:
                reasons.append("policy_revision_mismatch")
            if not ctx.approval_policy_hash:
                reasons.append("approval_policy_hash_missing")

        allowed = len(reasons) == 0
        context_hash = compute_context_hash(ctx.to_hash_dict())
        decision_hash = compute_decision_hash(
            allowed=allowed,
            phase=ctx.phase,
            capability_key=ctx.capability_key,
            policy_revision=ctx.policy_revision,
            engine_version=self.engine_version,
            context_hash=context_hash,
            reasons=reasons,
        )

        return ExecutionPolicyDecision(
            allowed=allowed,
            phase=ctx.phase,
            capability_key=ctx.capability_key,
            policy_revision=ctx.policy_revision,
            engine_version=self.engine_version,
            context_hash=context_hash,
            decision_hash=decision_hash,
            reasons=sorted(reasons),
        )

    async def evaluate_for_action(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis | None,
        *,
        phase: str,
        plan: ActionPlan,
        action: ActionProposal,
        registry: CapabilityRegistry,
        command: Any | None = None,
        operator_approved: bool | None = None,
        force_disabled: bool | None = None,
        current_snapshot_hash: str | None = None,
    ) -> ExecutionPolicyDecision:
        """Asynchronously assemble context from database and Redis, then evaluate."""
        from core.automation.contracts import compute_action_plan_hash
        from core.automation.persistence import canonical_params_hash
        from core.automation.worker_attestation import WorkerAttestationService
        from core.database.models import (
            ActionPreflightRecord,
            CapabilityExecutionPolicyRecord,
            CapabilityHealthRecord,
            ServiceRouteBindingRecord,
        )

        effective_force_disabled = (
            force_disabled if force_disabled is not None else is_deployment_force_disabled()
        )

        spec = registry.get(action.capability_key)
        executor = registry.get_executor(action.capability_key) if spec else None
        capability_registered = spec is not None
        executor_bound = executor is not None
        is_mutating = spec.is_mutating if spec else True

        # Binding check
        requires_binding = bool(plan.service_binding_key)
        binding_active = False
        binding_validated = False
        if requires_binding:
            binding = await session.scalar(
                select(ServiceRouteBindingRecord).where(
                    ServiceRouteBindingRecord.key == plan.service_binding_key,
                    ServiceRouteBindingRecord.version == plan.service_binding_version,
                    ServiceRouteBindingRecord.catalog_hash == plan.catalog_hash,
                )
            )
            if binding is not None:
                binding_active = bool(binding.is_active)
                binding_validated = bool(binding.is_validated)

        # Policy state
        policy_record = await session.get(CapabilityExecutionPolicyRecord, action.capability_key)
        policy_present = policy_record is not None
        desired_state = policy_record.desired_state if policy_record else "disabled"
        policy_revision = policy_record.revision if policy_record else None

        # Circuit breaker health
        health_record = await session.get(CapabilityHealthRecord, action.capability_key)
        circuit_broken = bool(health_record.is_circuit_broken) if health_record else False

        # Worker attestation
        worker_svc = WorkerAttestationService(self.engine_version)
        worker_res = await worker_svc.check_capability_worker(
            redis_client, capability_key=action.capability_key
        )
        worker_available = worker_res.worker_available
        worker_compatible = worker_res.worker_compatible
        worker_force_disabled = worker_res.worker_force_disabled

        # Hashes and preflight
        plan_valid = compute_action_plan_hash(plan) == plan.plan_hash
        snapshot_current = (
            (current_snapshot_hash == plan.snapshot_hash)
            if current_snapshot_hash is not None
            else True
        )
        expected_params_hash = canonical_params_hash(action.params)
        params_valid = bool(expected_params_hash)

        now = datetime.now(UTC)
        preflight_record = await session.scalar(
            select(ActionPreflightRecord)
            .where(
                ActionPreflightRecord.action_plan_id == plan.id,
                ActionPreflightRecord.action_id == action.id,
                ActionPreflightRecord.capability_key == action.capability_key,
                ActionPreflightRecord.snapshot_hash == plan.snapshot_hash,
                ActionPreflightRecord.plan_hash == plan.plan_hash,
                ActionPreflightRecord.params_hash == expected_params_hash,
            )
            .order_by(ActionPreflightRecord.created_at.desc())
            .limit(1)
        )
        preflight_passed = (
            preflight_record is not None
            and preflight_record.status in ("passed", "not_applicable")
        )
        exp_dt = preflight_record.expires_at if preflight_record else None
        if exp_dt is not None and exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=UTC)
        preflight_fresh = exp_dt is not None and exp_dt > now


        # Execution approval invariants
        if operator_approved is not None:
            is_op_approved = operator_approved
        else:
            is_op_approved = plan.state.value in ("approved", "running")

        approval_decision_id = getattr(command, "approval_policy_decision_id", None) if command else None
        approval_policy_revision = getattr(command, "approval_policy_revision", None) if command else None
        approval_policy_hash = getattr(command, "approval_policy_hash", None) if command else None

        ctx = ExecutionPolicyContext(
            phase=phase,
            capability_key=action.capability_key,
            is_mutating=is_mutating,
            requires_binding=requires_binding,
            capability_registered=capability_registered,
            executor_bound=executor_bound,
            binding_active=binding_active,
            binding_validated=binding_validated,
            policy_present=policy_present,
            desired_state=desired_state,
            policy_revision=policy_revision,
            force_disabled=effective_force_disabled,
            circuit_broken=circuit_broken,
            worker_available=worker_available,
            worker_compatible=worker_compatible,
            worker_force_disabled=worker_force_disabled,
            plan_valid=plan_valid,
            snapshot_current=snapshot_current,
            params_valid=params_valid,
            preflight_passed=preflight_passed,
            preflight_fresh=preflight_fresh,
            operator_approved=is_op_approved,
            approval_decision_id=approval_decision_id,
            approval_policy_revision=approval_policy_revision,
            approval_policy_hash=approval_policy_hash,
        )

        return self.evaluate(ctx)
