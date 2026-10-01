"""Canonical execution policy engine and decision evaluator for ADR 0006.

Provides a unified, domain-agnostic evaluation gate for both 'authorization'
and 'execution' phases across API and worker components.
"""

from __future__ import annotations

import hashlib
import json
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

ExecutionPhase = Literal["authorization", "execution", "approval"]


class ExecutionPolicyDecision(BaseModel):
    """Immutable, auditable evaluation decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    phase: str
    capability_key: str
    action_id: str | None = None
    authorization_kind: str | None = None
    policy_revision: int | None = None
    workflow_policy_revision: int | None = None
    engine_version: str = POLICY_ENGINE_VERSION
    context_hash: str
    decision_hash: str
    reasons: list[str] = Field(default_factory=list)
    context_json: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionPolicyContext:
    """Full operational context consumed by ExecutionPolicyEvaluator."""

    phase: str
    capability_key: str
    is_mutating: bool
    action_id: str | None = None
    authorization_kind: str | None = None  # "operator" | "policy_auto"

    # 1. Static capability catalog & executor
    capability_registered: bool = False
    executor_bound: bool = False
    auto_execution_eligible: bool = False

    # 2. Authorization & service route binding
    requires_binding: bool = False
    binding_present: bool = False
    binding_active: bool = False
    binding_validated: bool = False

    # 3. Administrative policy state from database
    policy_present: bool = False
    desired_state: str = "disabled"
    policy_revision: int | None = None

    # 4. Workflow execution mode policy
    workflow_key: str | None = None
    workflow_mode: str = "ASSISTED"  # "ASSISTED" | "FULL_AUTO"
    workflow_policy_revision: int | None = None

    # 5. Host/node-local deployment ceiling (independent fail-closed ceilings)
    api_force_disabled: bool = True
    worker_force_disabled: bool = False

    # 6. Technical health / circuit breaker
    circuit_broken: bool = False

    # 7. Worker attestation
    worker_available: bool = False
    worker_compatible: bool = False

    # 8. ActionPlan, snapshot and preflight integrity (params_valid removed)
    plan_valid: bool = False
    snapshot_current: bool = False
    preflight_passed: bool = False
    preflight_fresh: bool = False

    # 9. External domain guards (e.g. read-only pre-checks evaluated before gate)
    external_guard_reasons: tuple[str, ...] = ()

    # 10. Execution-phase authorization binding
    authorization_allowed: bool = False
    authorization_decision_id: UUID | None = None
    authorization_capability_policy_revision: int | None = None
    authorization_workflow_policy_revision: int | None = None
    authorization_decision_hash: str | None = None

    # Compatibility parameters (mapped in __post_init__)
    force_disabled: bool | None = None
    params_valid: bool | None = None
    operator_approved: bool | None = None
    approval_decision_id: UUID | None = None
    approval_policy_revision: int | None = None
    approval_policy_hash: str | None = None

    def __post_init__(self) -> None:
        if self.force_disabled is not None:
            object.__setattr__(self, "api_force_disabled", self.force_disabled)
        if self.approval_decision_id is not None and self.authorization_decision_id is None:
            object.__setattr__(self, "authorization_decision_id", self.approval_decision_id)
        if (
            self.approval_policy_revision is not None
            and self.authorization_capability_policy_revision is None
        ):
            object.__setattr__(
                self, "authorization_capability_policy_revision", self.approval_policy_revision
            )
        if self.approval_policy_hash is not None and self.authorization_decision_hash is None:
            object.__setattr__(self, "authorization_decision_hash", self.approval_policy_hash)
        if self.operator_approved is not None and not self.authorization_allowed:
            object.__setattr__(self, "authorization_allowed", self.operator_approved)
        if self.requires_binding and self.binding_active and not self.binding_present:
            object.__setattr__(self, "binding_present", True)

    def to_hash_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for legacy_k in (
            "force_disabled",
            "params_valid",
            "operator_approved",
            "approval_decision_id",
            "approval_policy_revision",
            "approval_policy_hash",
        ):
            data.pop(legacy_k, None)
        if self.authorization_decision_id is not None:
            data["authorization_decision_id"] = str(self.authorization_decision_id)
        data["external_guard_reasons"] = list(self.external_guard_reasons)
        return data


def compute_context_hash(context_dict: dict[str, Any]) -> str:
    canonical_repr = json.dumps(context_dict, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def compute_decision_hash(
    *,
    allowed: bool,
    phase: str,
    capability_key: str,
    action_id: str | None = None,
    authorization_kind: str | None = None,
    policy_revision: int | None = None,
    workflow_policy_revision: int | None = None,
    engine_version: str = POLICY_ENGINE_VERSION,
    context_hash: str,
    reasons: list[str],
) -> str:
    payload: dict[str, Any] = {
        "allowed": allowed,
        "capability_key": capability_key,
        "context_hash": context_hash,
        "engine_version": engine_version,
        "phase": phase,
        "policy_revision": policy_revision,
        "reasons": sorted(reasons),
    }
    if action_id is not None:
        payload["action_id"] = action_id
    if authorization_kind is not None:
        payload["authorization_kind"] = authorization_kind
    if workflow_policy_revision is not None:
        payload["workflow_policy_revision"] = workflow_policy_revision
    canonical_repr = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def is_deployment_force_disabled() -> bool:
    """Compatibility shim: manual deployment kill switches were removed."""
    return False


def _extract_command_fields(command: Any) -> tuple[UUID | None, int | None, int | None, str | None]:
    if not command:
        return None, None, None, None
    auth_decision_id = None
    for attr in ("authorization_decision_id", "approval_policy_decision_id"):
        if hasattr(command, attr):
            v = getattr(command, attr)
            if isinstance(v, (UUID, str)):
                auth_decision_id = UUID(str(v)) if not isinstance(v, UUID) else v
                break
    auth_cap_rev = None
    for attr in ("authorization_capability_policy_revision", "approval_policy_revision"):
        if hasattr(command, attr):
            v = getattr(command, attr)
            if isinstance(v, int):
                auth_cap_rev = v
                break
    auth_wf_rev = None
    if hasattr(command, "authorization_workflow_policy_revision"):
        v = command.authorization_workflow_policy_revision
        if isinstance(v, int):
            auth_wf_rev = v
    auth_decision_hash = None
    for attr in ("authorization_decision_hash", "approval_policy_hash"):
        if hasattr(command, attr):
            v = getattr(command, attr)
            if isinstance(v, str):
                auth_decision_hash = v
                break
    return auth_decision_id, auth_cap_rev, auth_wf_rev, auth_decision_hash


class ExecutionPolicyEvaluator:
    """Canonical evaluator: pure conjunction of all safety, administrative and technical gates."""

    def __init__(self, engine_version: str = POLICY_ENGINE_VERSION) -> None:
        self.engine_version = engine_version

    def evaluate(self, ctx: ExecutionPolicyContext) -> ExecutionPolicyDecision:
        reasons: list[str] = []

        # 0. External domain guards (e.g. read-only pre-validation)
        reasons.extend(ctx.external_guard_reasons)

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

        # 4. ActionPlan, snapshot and preflight integrity
        if not ctx.plan_valid:
            reasons.append("plan_invalid")
        if not ctx.snapshot_current:
            reasons.append("snapshot_stale")
        if not ctx.preflight_passed:
            reasons.append("preflight_not_passed")
        elif not ctx.preflight_fresh:
            reasons.append("preflight_expired")

        # Mutating capability gates
        if ctx.is_mutating:
            # Authorization scope / service route binding
            if ctx.requires_binding:
                if not ctx.binding_present:
                    reasons.append("binding_missing")
                else:
                    if not ctx.binding_active:
                        reasons.append("binding_not_active")
                    if not ctx.binding_validated:
                        reasons.append("binding_not_validated")

            # Administrative policy state from database
            if not ctx.policy_present:
                reasons.append("policy_not_found")
            elif ctx.desired_state != "enabled":
                reasons.append("policy_disabled")

            # Host-local deployment ceiling (API / node local)
            if ctx.api_force_disabled:
                reasons.append("deployment_force_disabled")

        # Normalized phase
        normalized_phase = "authorization" if ctx.phase in ("authorization", "approval") else "execution"

        if normalized_phase == "authorization":
            if ctx.authorization_kind == "policy_auto":
                if ctx.workflow_mode != "FULL_AUTO":
                    reasons.append("workflow_mode_not_auto")
                if not ctx.auto_execution_eligible:
                    reasons.append("capability_not_auto_eligible")
        elif normalized_phase == "execution":
            if not ctx.authorization_allowed:
                reasons.append("authorization_not_allowed")
                if ctx.operator_approved is False:
                    reasons.append("operator_approval_missing")
            if ctx.authorization_decision_id is None:
                reasons.append("authorization_decision_missing")
            if not ctx.authorization_decision_hash:
                reasons.append("authorization_decision_hash_missing")
            if (
                ctx.authorization_capability_policy_revision is None
                or ctx.authorization_capability_policy_revision != ctx.policy_revision
            ):
                reasons.append("policy_revision_mismatch")
            if (
                ctx.workflow_policy_revision is not None
                and ctx.authorization_workflow_policy_revision is not None
                and ctx.authorization_workflow_policy_revision != ctx.workflow_policy_revision
            ):
                reasons.append("workflow_policy_revision_mismatch")

        allowed = len(reasons) == 0
        context_dict = ctx.to_hash_dict()
        context_hash = compute_context_hash(context_dict)
        decision_hash = compute_decision_hash(
            allowed=allowed,
            phase=ctx.phase,
            capability_key=ctx.capability_key,
            action_id=ctx.action_id,
            authorization_kind=ctx.authorization_kind,
            policy_revision=ctx.policy_revision,
            workflow_policy_revision=ctx.workflow_policy_revision,
            engine_version=self.engine_version,
            context_hash=context_hash,
            reasons=reasons,
        )

        return ExecutionPolicyDecision(
            allowed=allowed,
            phase=ctx.phase,
            capability_key=ctx.capability_key,
            action_id=ctx.action_id,
            authorization_kind=ctx.authorization_kind,
            policy_revision=ctx.policy_revision,
            workflow_policy_revision=ctx.workflow_policy_revision,
            engine_version=self.engine_version,
            context_hash=context_hash,
            decision_hash=decision_hash,
            reasons=sorted(reasons),
            context_json=context_dict,
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
        authorization_kind: str | None = None,
        operator_approved: bool | None = None,
        force_disabled: bool | None = None,
        current_snapshot_hash: str | None = None,
        external_guard_reasons: tuple[str, ...] = (),
    ) -> ExecutionPolicyDecision:
        """Asynchronously assemble context from database and Redis, then evaluate."""
        from core.automation.contracts import compute_action_plan_hash
        from core.automation.persistence import canonical_params_hash
        from core.automation.worker_attestation import WorkerAttestationService
        from core.database.models import (
            ActionPreflightRecord,
            CapabilityExecutionPolicyRecord,
            CapabilityHealthRecord,
            ExecutionPolicyDecisionRecord,
            ServiceRouteBindingRecord,
            WorkflowExecutionModeRecord,
        )

        effective_force_disabled = (
            force_disabled if force_disabled is not None else is_deployment_force_disabled()
        )

        spec = registry.get(action.capability_key)
        executor = registry.get_executor(action.capability_key) if spec else None
        capability_registered = spec is not None
        executor_bound = executor is not None
        is_mutating = spec.is_mutating if spec else True
        auto_execution_eligible = spec.auto_execution_eligible if spec else False

        # Binding check
        requires_binding = bool(spec and spec.requires_service_binding)
        binding_present = False
        binding_active = False
        binding_validated = False
        if requires_binding:
            if plan.service_binding_key and plan.service_binding_version and plan.catalog_hash:
                binding = await session.scalar(
                    select(ServiceRouteBindingRecord).where(
                        ServiceRouteBindingRecord.key == plan.service_binding_key,
                        ServiceRouteBindingRecord.version == plan.service_binding_version,
                        ServiceRouteBindingRecord.catalog_hash == plan.catalog_hash,
                    )
                )
                if binding is not None:
                    binding_present = True
                    binding_active = bool(binding.is_active)
                    binding_validated = bool(binding.is_validated)
        else:
            binding_present = True
            binding_active = True
            binding_validated = True

        # Policy state
        policy_record = await session.get(CapabilityExecutionPolicyRecord, action.capability_key)
        policy_present = policy_record is not None
        desired_state = policy_record.desired_state if policy_record else "disabled"
        policy_revision = policy_record.revision if policy_record else None

        # Workflow execution mode state
        mode_record = await session.get(WorkflowExecutionModeRecord, plan.workflow_key)
        workflow_mode = mode_record.desired_mode if mode_record else "ASSISTED"
        workflow_policy_revision = mode_record.revision if mode_record else 1

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

        # Execution authorization invariants
        auth_decision_id, auth_cap_revision, auth_wf_revision, auth_decision_hash = (
            _extract_command_fields(command)
        )

        auth_allowed = False
        resolved_auth_kind = authorization_kind
        if auth_decision_id is not None:
            auth_decision_rec = await session.get(ExecutionPolicyDecisionRecord, auth_decision_id)
            if auth_decision_rec is not None:
                if resolved_auth_kind is None:
                    resolved_auth_kind = auth_decision_rec.authorization_kind
                auth_allowed = bool(
                    auth_decision_rec.allowed
                    and auth_decision_rec.phase in ("authorization", "approval")
                    and auth_decision_rec.capability_key == action.capability_key
                    and auth_decision_rec.decision_hash == auth_decision_hash
                )
            elif operator_approved is not None:
                auth_allowed = operator_approved
            elif plan.state.value in ("authorized", "approved", "running"):
                auth_allowed = True
        elif operator_approved is not None:
            auth_allowed = operator_approved
            if resolved_auth_kind is None and operator_approved:
                resolved_auth_kind = "operator"
        elif plan.state.value in ("authorized", "approved", "running"):
            auth_allowed = True
            if resolved_auth_kind is None:
                resolved_auth_kind = "operator"

        ctx = ExecutionPolicyContext(
            phase=phase,
            capability_key=action.capability_key,
            is_mutating=is_mutating,
            action_id=action.id,
            authorization_kind=resolved_auth_kind,
            capability_registered=capability_registered,
            executor_bound=executor_bound,
            auto_execution_eligible=auto_execution_eligible,
            requires_binding=requires_binding,
            binding_present=binding_present,
            binding_active=binding_active,
            binding_validated=binding_validated,
            policy_present=policy_present,
            desired_state=desired_state,
            policy_revision=policy_revision,
            workflow_key=plan.workflow_key,
            workflow_mode=workflow_mode,
            workflow_policy_revision=workflow_policy_revision,
            api_force_disabled=effective_force_disabled,
            worker_force_disabled=worker_force_disabled,
            circuit_broken=circuit_broken,
            worker_available=worker_available,
            worker_compatible=worker_compatible,
            plan_valid=plan_valid,
            snapshot_current=snapshot_current,
            preflight_passed=preflight_passed,
            preflight_fresh=preflight_fresh,
            external_guard_reasons=external_guard_reasons,
            authorization_allowed=auth_allowed,
            authorization_decision_id=auth_decision_id,
            authorization_capability_policy_revision=auth_cap_revision,
            authorization_workflow_policy_revision=auth_wf_revision,
            authorization_decision_hash=auth_decision_hash,
            operator_approved=operator_approved,
        )

        return self.evaluate(ctx)
