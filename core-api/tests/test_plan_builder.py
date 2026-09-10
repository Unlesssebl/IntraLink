"""Tests for PlanBuilder, ExecutionPlan and contract invariants."""

import datetime as dt
import pytest

from shared.domain import (
    ExecutionPlan,
    FactBag,
    FactObservation,
    FactSource,
    FactState,
    LegacyExecutionPlan,
    LegacyPlanStep,
    PlanStep,
    StepKind,
    StepStatus,
    parse_execution_plan,
)
from app.services.plan_builder import PlanBuilder, _is_diagnostic_fresh


def test_plan_step_completed_invariant():
    """Completed step requires non-empty evidence_refs."""
    with pytest.raises(ValueError, match="must have non-empty evidence_refs"):
        PlanStep(
            id="s1",
            title="Step 1",
            kind=StepKind.CHECK,
            status=StepStatus.COMPLETED,
            evidence_refs=[],
        )

    # Valid completed check step
    step = PlanStep(
        id="s1",
        title="Step 1",
        kind=StepKind.CHECK,
        status=StepStatus.COMPLETED,
        evidence_refs=["diag:host:online"],
    )
    assert step.status == StepStatus.COMPLETED


def test_plan_step_manual_completed_invariant():
    """Manual completed step requires completed_by and completed_at."""
    with pytest.raises(ValueError, match="requires completed_by and completed_at"):
        PlanStep(
            id="m1",
            title="Manual 1",
            kind=StepKind.MANUAL,
            status=StepStatus.COMPLETED,
            evidence_refs=["manual:done"],
        )

    valid_manual = PlanStep(
        id="m1",
        title="Manual 1",
        kind=StepKind.MANUAL,
        status=StepStatus.COMPLETED,
        evidence_refs=["manual:done"],
        completed_by="engineer:belikov.a",
        completed_at="2026-09-10T12:00:00Z",
    )
    assert valid_manual.completed_by == "engineer:belikov.a"


def test_plan_step_skipped_invariant():
    """Skipped step requires skip_reason."""
    with pytest.raises(ValueError, match="requires skip_reason"):
        PlanStep(
            id="s2",
            title="Step 2",
            kind=StepKind.CHECK,
            status=StepStatus.SKIPPED,
        )

    valid_skipped = PlanStep(
        id="s2",
        title="Step 2",
        kind=StepKind.CHECK,
        status=StepStatus.SKIPPED,
        skip_reason="Не применимо для локального USB-подключения",
    )
    assert valid_skipped.skip_reason is not None


def test_legacy_execution_plan_parsing():
    """Backward compatibility for ExecutionPlan v1."""
    legacy_data = {
        "schema_version": 1,
        "scenario_key": "install_printer",
        "scenario_version": 1,
        "fact_revision": 0,
        "steps": [
            {"id": "collect_1", "kind": "collect"},
            {"id": "dispatch_1", "kind": "dispatch"},
        ],
    }
    parsed = parse_execution_plan(legacy_data)
    assert isinstance(parsed, LegacyExecutionPlan)
    assert parsed.schema_version == 1
    assert len(parsed.steps) == 2


def test_plan_builder_all_nine_scenario_keys():
    """All 9 canonical scenarios build valid ExecutionPlan v2."""
    keys = [
        "install_printer",
        "printer_hardware_service",
        "printer_print_failure",
        "printer_scan_failure",
        "pc_performance",
        "file_lock",
        "peripheral_setup",
        "peripheral_diagnostics",
        "os_reinstallation",
    ]
    for key in keys:
        plan = PlanBuilder.build(scenario_key=key, scenario_version=1)
        assert isinstance(plan, ExecutionPlan)
        assert plan.schema_version == 2
        assert plan.scenario_key == key
        assert len(plan.steps) >= 3
        assert plan.phase in ("collecting", "ready", "running", "verifying", "completed", "blocked", "dispatched")


def test_install_printer_plan_flow():
    """Check install_printer plan states from missing facts to verified."""
    from app.services.facts import merge_observations
    obs = [
        FactObservation(key="pc_name", value="NTEMW0100", state=FactState.VALID, source=FactSource.STRUCTURED_FIELD, source_ref="task"),
        FactObservation(key="printer_name", value="Kyocera", state=FactState.VALID, source=FactSource.STRUCTURED_FIELD, source_ref="task"),
    ]
    bag = merge_observations(obs)
    diag = {"host": "NTEMW0100", "ping": True, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
    plan = PlanBuilder.build(scenario_key="install_printer", scenario_version=2, facts=bag, diagnostics=diag)

    assert plan.phase == "ready"
    assert plan.steps[0].id == "check_params"
    assert plan.steps[0].status == StepStatus.COMPLETED
    assert plan.steps[1].id == "preflight_diagnostics"
    assert plan.steps[1].status == StepStatus.COMPLETED
    assert plan.steps[3].id == "install_printer"
    assert plan.steps[3].required_for_resolution is True


def test_diagnostic_ttl_and_target_binding():
    """Diagnostic for another host or expired TTL must not mark step completed."""
    # Another host
    assert not _is_diagnostic_fresh({"host": "OTHER_PC", "ping": True}, expected_target="MY_PC")
    # Same host fresh
    assert _is_diagnostic_fresh({"host": "MY_PC", "ping": True}, expected_target="MY_PC")
    # Expired TTL (1 hour ago)
    old_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
    assert not _is_diagnostic_fresh({"host": "MY_PC", "ping": True, "timestamp": old_time}, expected_target="MY_PC")
