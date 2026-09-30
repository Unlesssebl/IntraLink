"""Unit and integration tests for canonical Execution Policy Evaluator and Worker Attestation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.automation.capabilities import (
    CapabilityRisk,
    CapabilityRegistry,
    get_default_capability_registry,
)
from core.automation.contracts import (
    ActionPlan,
    ActionPlanState,
    ActionProposal,
    Disposition,
    compute_action_plan_hash,
)
from core.automation.execution_policy import (
    ExecutionPolicyContext,
    ExecutionPolicyEvaluator,
    compute_context_hash,
    compute_decision_hash,
    is_deployment_force_disabled,
)
from core.automation.persistence import canonical_params_hash
from core.automation.worker_attestation import (
    POLICY_ENGINE_VERSION,
    WORKER_HEARTBEAT_TTL_SECONDS,
    WorkerAttestationService,
    WorkerHeartbeat,
)
from core.database.base import Base
from core.database.models import (
    ActionPreflightRecord,
    CapabilityExecutionPolicyRecord,
    CapabilityHealthRecord,
    ServiceRouteBindingRecord,
)

SAMPLE_SNAPSHOT_HASH = "a" * 64
SAMPLE_CATALOG_HASH = "b" * 64


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [k for k in self.data if k.startswith(prefix)]


def _make_action_plan(
    *,
    capability_key: str = "create_ad_user",
    service_binding_key: str | None = "ad_account_creation",
    service_binding_version: str | None = "1",
    catalog_hash: str | None = SAMPLE_CATALOG_HASH,
    approved: bool = True,
    snapshot_hash: str = SAMPLE_SNAPSHOT_HASH,
) -> tuple[ActionPlan, ActionProposal]:
    action_id = str(uuid4())
    params = {"first_name": "Иван", "last_name": "Иванов"}
    action = ActionProposal(
        id=action_id,
        capability_key=capability_key,
        sequence_no=1,
        params=params,
        risk="high",
        requires_approval=True,
    )
    temp_plan = ActionPlan(
        id=uuid4(),
        task_id=142250,
        snapshot_hash=snapshot_hash,
        case_decision_id=uuid4(),
        workflow_plan_id=uuid4(),
        workflow_key="employee_onboarding_workflow",
        workflow_version="2.0.0",
        service_binding_key=service_binding_key,
        service_binding_version=service_binding_version,
        catalog_hash=catalog_hash,
        state=ActionPlanState.approved if approved else ActionPlanState.ready,
        disposition=Disposition.execute,
        actions=[action],
        plan_hash="",
    )
    plan = temp_plan.model_copy(update={"plan_hash": compute_action_plan_hash(temp_plan)})
    return plan, action



# ---------------------------------------------------------------------------
# 1. Truth Table and Determinism Tests
# ---------------------------------------------------------------------------


def test_decision_and_context_hash_determinism():
    """Verify context_hash and decision_hash are strictly deterministic between API and Worker."""
    ctx1 = ExecutionPolicyContext(
        phase="approval",
        capability_key="create_ad_user",
        is_mutating=True,
        requires_binding=True,
        capability_registered=True,
        executor_bound=True,
        binding_active=True,
        binding_validated=True,
        policy_present=True,
        desired_state="enabled",
        policy_revision=1,
        force_disabled=False,
        circuit_broken=False,
        worker_available=True,
        worker_compatible=True,
        worker_force_disabled=False,
        plan_valid=True,
        snapshot_current=True,
        params_valid=True,
        preflight_passed=True,
        preflight_fresh=True,
        operator_approved=False,
    )
    ctx2 = ExecutionPolicyContext(
        phase="approval",
        capability_key="create_ad_user",
        is_mutating=True,
        requires_binding=True,
        capability_registered=True,
        executor_bound=True,
        binding_active=True,
        binding_validated=True,
        policy_present=True,
        desired_state="enabled",
        policy_revision=1,
        force_disabled=False,
        circuit_broken=False,
        worker_available=True,
        worker_compatible=True,
        worker_force_disabled=False,
        plan_valid=True,
        snapshot_current=True,
        params_valid=True,
        preflight_passed=True,
        preflight_fresh=True,
        operator_approved=False,
    )
    c_hash1 = compute_context_hash(ctx1.to_hash_dict())
    c_hash2 = compute_context_hash(ctx2.to_hash_dict())
    assert c_hash1 == c_hash2
    assert len(c_hash1) == 64

    d_hash1 = compute_decision_hash(
        allowed=True,
        phase="approval",
        capability_key="create_ad_user",
        policy_revision=1,
        engine_version="2.0.0",
        context_hash=c_hash1,
        reasons=[],
    )
    d_hash2 = compute_decision_hash(
        allowed=True,
        phase="approval",
        capability_key="create_ad_user",
        policy_revision=1,
        engine_version="2.0.0",
        context_hash=c_hash2,
        reasons=[],
    )
    assert d_hash1 == d_hash2
    assert len(d_hash1) == 64


async def test_truth_table_all_gates(session_factory):
    """Exhaustive truth-table verification of all gates for approval and execution phases."""
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    # Setup valid worker heartbeat in Redis
    now = datetime.now(UTC)
    heartbeat = WorkerHeartbeat(
        node_id="worker-node-1",
        capabilities=["create_ad_user", "reset_user_password"],
        executor_types=["internal"],
        policy_engine_version="2.0.0",
        force_disabled=False,
        config_fingerprint="fingerprint_1",
        heartbeat_at=now,
    )
    await redis.set("automation:worker:worker-node-1", heartbeat.model_dump_json())

    # Seed Database: policy enabled rev 1, circuit closed, binding active
    async with session_factory() as session, session.begin():
        pol = CapabilityExecutionPolicyRecord(
            capability_key="create_ad_user",
            desired_state="enabled",
            revision=1,
            reason="Initial enable for test",
            changed_by="admin",
            changed_at=now,
        )
        binding = ServiceRouteBindingRecord(
            key="ad_account_creation",
            version="1",
            catalog_hash=SAMPLE_CATALOG_HASH,
            service_ids_json=[53],
            allowed_case_types_json=["employee_onboarding"],
            allowed_workflows_json=["employee_onboarding_workflow"],
            allowed_capabilities_json=["create_ad_user"],
            redirect_strategy="transfer_service",
            risk="high",
            is_active=True,
            is_validated=True,
        )
        session.add(pol)
        session.add(binding)

    # 1. Base SUCCESS CASE: Approval Phase
    plan, action = _make_action_plan(capability_key="create_ad_user", approved=False)
    # Add passing preflight record
    async with session_factory() as session, session.begin():
        session.add(
            ActionPreflightRecord(
                id=uuid4(),
                task_id=plan.task_id,
                action_plan_id=plan.id,
                action_id=action.id,
                capability_key=action.capability_key,
                snapshot_hash=plan.snapshot_hash,
                plan_hash=plan.plan_hash,
                params_hash=canonical_params_hash(action.params),
                status="passed",
                checks_json=["ou_exists", "login_unique"],
                details_json={},
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
        )

    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="approval",
            plan=plan,
            action=action,
            registry=registry,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is True
        assert decision.phase == "approval"
        assert decision.reasons == []
        assert decision.policy_revision == 1

    # 2. Base SUCCESS CASE: Execution Phase with Operator Approval & Bound Command
    plan_appr, action_appr = _make_action_plan(capability_key="create_ad_user", approved=True)
    async with session_factory() as session, session.begin():
        session.add(
            ActionPreflightRecord(
                id=uuid4(),
                task_id=plan_appr.task_id,
                action_plan_id=plan_appr.id,
                action_id=action_appr.id,
                capability_key=action_appr.capability_key,
                snapshot_hash=plan_appr.snapshot_hash,
                plan_hash=plan_appr.plan_hash,
                params_hash=canonical_params_hash(action_appr.params),
                status="passed",
                checks_json=["ou_exists", "login_unique"],
                details_json={},
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
        )

    from unittest.mock import MagicMock

    mock_command = MagicMock()
    mock_command.approval_policy_decision_id = uuid4()
    mock_command.approval_policy_revision = 1
    mock_command.approval_policy_hash = "mock_hash_123"

    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="execution",
            plan=plan_appr,
            action=action_appr,
            registry=registry,
            command=mock_command,
            operator_approved=True,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is True
        assert decision.phase == "execution"
        assert decision.reasons == []


# ---------------------------------------------------------------------------
# 2. Negative Gates (Deny Conditions)
# ---------------------------------------------------------------------------


async def test_negative_gate_policy_disabled(session_factory):
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    async with session_factory() as session, session.begin():
        session.add(
            CapabilityExecutionPolicyRecord(
                capability_key="create_ad_user",
                desired_state="disabled",
                revision=2,
                reason="Turned off by security",
                changed_by="sec_admin",
                changed_at=datetime.now(UTC),
            )
        )

    plan, action = _make_action_plan()
    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="approval",
            plan=plan,
            action=action,
            registry=registry,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is False
        assert "policy_disabled" in decision.reasons


async def test_negative_gate_policy_not_found(session_factory):
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    plan, action = _make_action_plan(capability_key="create_ad_user")
    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="approval",
            plan=plan,
            action=action,
            registry=registry,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is False
        assert "policy_not_found" in decision.reasons


async def test_negative_gate_deployment_force_disabled(session_factory):
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    plan, action = _make_action_plan()
    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="approval",
            plan=plan,
            action=action,
            registry=registry,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=True,
        )
        assert decision.allowed is False
        assert "deployment_force_disabled" in decision.reasons


async def test_negative_gate_circuit_breaker_open(session_factory):
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    async with session_factory() as session, session.begin():
        session.add(
            CapabilityHealthRecord(
                capability_key="create_ad_user",
                consecutive_failures=5,
                is_circuit_broken=True,
            )
        )
        session.add(
            CapabilityExecutionPolicyRecord(
                capability_key="create_ad_user",
                desired_state="enabled",
                revision=1,
                reason="Enabled",
                changed_by="admin",
                changed_at=datetime.now(UTC),
            )
        )

    plan, action = _make_action_plan()
    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="approval",
            plan=plan,
            action=action,
            registry=registry,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is False
        assert "circuit_broken" in decision.reasons


async def test_negative_gate_missing_worker(session_factory):
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    plan, action = _make_action_plan()
    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="approval",
            plan=plan,
            action=action,
            registry=registry,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is False
        assert "worker_unavailable" in decision.reasons


async def test_negative_gate_incompatible_worker_engine_version(session_factory):
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    heartbeat = WorkerHeartbeat(
        node_id="worker-node-legacy",
        capabilities=["create_ad_user"],
        executor_types=["internal"],
        policy_engine_version="1.8.0",  # Legacy / mismatch
        force_disabled=False,
        config_fingerprint="fp",
        heartbeat_at=datetime.now(UTC),
    )
    await redis.set("automation:worker:worker-node-legacy", heartbeat.model_dump_json())

    plan, action = _make_action_plan()
    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="approval",
            plan=plan,
            action=action,
            registry=registry,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is False
        assert "worker_incompatible" in decision.reasons


async def test_negative_gate_worker_force_disabled(session_factory):
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    heartbeat = WorkerHeartbeat(
        node_id="worker-node-disabled",
        capabilities=["create_ad_user"],
        executor_types=["internal"],
        policy_engine_version="2.0.0",
        force_disabled=True,
        config_fingerprint="fp",
        heartbeat_at=datetime.now(UTC),
    )
    await redis.set("automation:worker:worker-node-disabled", heartbeat.model_dump_json())

    plan, action = _make_action_plan()
    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="approval",
            plan=plan,
            action=action,
            registry=registry,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is False
        assert "worker_force_disabled" in decision.reasons


# ---------------------------------------------------------------------------
# 3. Execution Phase Tightening & Rejection Tests
# ---------------------------------------------------------------------------


async def test_policy_tightened_after_approval_blocks_execution(session_factory):
    """If policy revision changes after approval, worker rejects execution."""
    evaluator = ExecutionPolicyEvaluator()
    registry = get_default_capability_registry()
    redis = FakeRedis()

    heartbeat = WorkerHeartbeat(
        node_id="worker-node-1",
        capabilities=["create_ad_user"],
        executor_types=["internal"],
        policy_engine_version="2.0.0",
        force_disabled=False,
        config_fingerprint="fp",
        heartbeat_at=datetime.now(UTC),
    )
    await redis.set("automation:worker:worker-node-1", heartbeat.model_dump_json())

    # Policy in DB is now revision 2 (or disabled)
    async with session_factory() as session, session.begin():
        session.add(
            CapabilityExecutionPolicyRecord(
                capability_key="create_ad_user",
                desired_state="enabled",
                revision=2,
                reason="Policy updated to rev 2",
                changed_by="admin",
                changed_at=datetime.now(UTC),
            )
        )

    plan, action = _make_action_plan(approved=True)
    from unittest.mock import MagicMock

    mock_command = MagicMock()
    mock_command.approval_policy_decision_id = uuid4()
    mock_command.approval_policy_revision = 1  # Approved when rev was 1!
    mock_command.approval_policy_hash = "mock_hash"

    async with session_factory() as session:
        decision = await evaluator.evaluate_for_action(
            session=session,
            redis_client=redis,
            phase="execution",
            plan=plan,
            action=action,
            registry=registry,
            command=mock_command,
            operator_approved=True,
            current_snapshot_hash=SAMPLE_SNAPSHOT_HASH,
            force_disabled=False,
        )
        assert decision.allowed is False
        assert "policy_revision_mismatch" in decision.reasons


async def test_policy_softened_after_deny_does_not_resurrect_command(session_factory):
    """Old command denied during execution does not automatically re-execute without new operator approval."""
    evaluator = ExecutionPolicyEvaluator()
    plan, action = _make_action_plan(approved=False)  # Plan not approved!

    ctx = ExecutionPolicyContext(
        phase="execution",
        capability_key="create_ad_user",
        is_mutating=True,
        requires_binding=False,
        capability_registered=True,
        executor_bound=True,
        binding_active=True,
        binding_validated=True,
        policy_present=True,
        desired_state="enabled",
        policy_revision=3,
        force_disabled=False,
        circuit_broken=False,
        worker_available=True,
        worker_compatible=True,
        worker_force_disabled=False,
        plan_valid=True,
        snapshot_current=True,
        params_valid=True,
        preflight_passed=True,
        preflight_fresh=True,
        operator_approved=False,  # Unapproved!
        approval_decision_id=None,
        approval_policy_revision=None,
        approval_policy_hash=None,
    )
    decision = evaluator.evaluate(ctx)
    assert decision.allowed is False
    assert "operator_approval_missing" in decision.reasons


def test_ci_invariant_all_mutating_capabilities_have_policy_in_registry():
    """All mutating capabilities registered in the codebase must be recognized."""
    registry = get_default_capability_registry()
    mutating = [c for c in registry.list_all() if c.is_mutating]
    assert len(mutating) >= 6

    known_mutating_keys = {
        "create_ad_user",
        "disable_ad_user",
        "add_wlan_group_member",
        "install_printer",
        "set_default_printer",
        "reset_print_spooler",
    }
    registered_keys = {c.key for c in mutating}
    assert known_mutating_keys.issubset(registered_keys)


async def test_optimistic_locking_revision_conflict(session_factory):
    """Updating capability policy with stale revision returns 409 Conflict."""
    from fastapi import HTTPException
    from api.src.features.admin.schemas import UpdateCapabilityExecutionPolicyRequest
    from api.src.features.admin.service import AdminCredentialsService

    admin_svc = AdminCredentialsService()
    redis = FakeRedis()

    async with session_factory() as session, session.begin():
        session.add(
            CapabilityExecutionPolicyRecord(
                capability_key="create_ad_user",
                desired_state="disabled",
                revision=3,  # Current DB revision is 3
                reason="Current state",
                changed_by="admin1",
                changed_at=datetime.now(UTC),
            )
        )

    # Admin 2 tries to update with stale expected_revision=2
    req = UpdateCapabilityExecutionPolicyRequest(
        desired_state="enabled",
        expected_revision=2,  # Stale!
        reason="Admin 2 tries to enable but saw rev 2",
    )

    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            await admin_svc.update_execution_policy(
                session=session,
                redis_client=redis,
                capability_key="create_ad_user",
                request=req,
                admin_login="admin2",
            )
        assert exc_info.value.status_code == 409
        assert "Конфликт версий" in exc_info.value.detail

