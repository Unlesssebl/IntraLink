import pytest
from pydantic import ValidationError

from core.automation.capabilities import get_default_capability_registry
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import (
    AssertionKind,
    CaseAssertion,
    CaseCandidate,
    CaseDecision,
    CaseDecisionState,
    CaseFrame,
    Disposition,
    ExtractionMethod,
    WorkflowPlanState,
    compute_action_plan_hash,
)
from core.automation.workflows import get_default_workflow_registry

SNAPSHOT_HASH = "a" * 64


def _frame(**entities: str) -> CaseFrame:
    return CaseFrame(
        task_id=42,
        snapshot_hash=SNAPSHOT_HASH,
        frame_version="case-frame-v1",
        entities=entities,
    )


def _decision(frame: CaseFrame, case_type: str) -> CaseDecision:
    return CaseDecision(
        task_id=frame.task_id,
        snapshot_hash=frame.snapshot_hash,
        frame_id=frame.id,
        router_version="case-router-v1",
        state=CaseDecisionState.selected,
        primary_case_type=case_type,
        candidates=[CaseCandidate(case_type=case_type, case_type_version="1.0.0")],
    )


def _compiler() -> WorkflowCompiler:
    return WorkflowCompiler(get_default_workflow_registry(), get_default_capability_registry())


def test_llm_assertion_requires_literal_span() -> None:
    with pytest.raises(ValidationError):
        CaseAssertion(
            id="a1",
            kind=AssertionKind.intent,
            key="connect_printer",
            value="true",
            source_ref="description",
            extraction_method=ExtractionMethod.llm,
        )


def test_case_decision_cannot_contain_unregistered_choice() -> None:
    frame = _frame()
    with pytest.raises(ValidationError):
        CaseDecision(
            task_id=42,
            snapshot_hash=SNAPSHOT_HASH,
            frame_id=frame.id,
            router_version="case-router-v1",
            state=CaseDecisionState.selected,
            primary_case_type="printing_incident",
            candidates=[],
        )


def test_missing_workflow_facts_produce_clarification_not_action() -> None:
    frame = _frame()
    workflow, action_plan = _compiler().compile(
        frame=frame,
        decision=_decision(frame, "printer_connection_request"),
    )

    assert workflow.state == WorkflowPlanState.awaiting_facts
    assert workflow.disposition == Disposition.clarify
    assert workflow.missing_facts == ["pc_name", "connection_type"]
    assert action_plan is None


def test_printer_connection_compiles_to_install_capability() -> None:
    frame = _frame(pc_name="WKS-01", connection_type="network", printer_address="10.20.30.40")
    workflow, action_plan = _compiler().compile(
        frame=frame,
        decision=_decision(frame, "printer_connection_request"),
    )

    assert workflow.state == WorkflowPlanState.awaiting_approval
    assert action_plan is not None
    assert [item.capability_key for item in action_plan.actions] == ["install_printer"]
    assert action_plan.plan_hash == compute_action_plan_hash(action_plan)


def test_printing_incident_does_not_guess_technical_action() -> None:
    frame = _frame(pc_name="WKS-01")
    workflow, action_plan = _compiler().compile(
        frame=frame,
        decision=_decision(frame, "printing_incident"),
    )

    assert workflow.state == WorkflowPlanState.awaiting_diagnostics
    assert workflow.disposition == Disposition.manual
    assert action_plan is None


def test_printing_incident_uses_explicit_queue_symptom() -> None:
    assertion = CaseAssertion(
        id="symptom-1",
        kind=AssertionKind.symptom,
        key="stuck_print_queue",
        value="true",
        source_ref="description",
        text_span="документы висят в очереди",
        extraction_method=ExtractionMethod.deterministic,
    )
    frame = CaseFrame(
        task_id=42,
        snapshot_hash=SNAPSHOT_HASH,
        frame_version="case-frame-v1",
        entities={"pc_name": "WKS-01"},
        assertions=[assertion],
    )
    _, action_plan = _compiler().compile(
        frame=frame,
        decision=_decision(frame, "printing_incident"),
    )

    assert action_plan is not None
    assert action_plan.actions[0].capability_key == "reset_print_spooler"


def test_multi_intent_requires_operator_composition() -> None:
    frame = _frame(pc_name="WKS-01")
    decision = CaseDecision(
        task_id=42,
        snapshot_hash=SNAPSHOT_HASH,
        frame_id=frame.id,
        router_version="case-router-v1",
        state=CaseDecisionState.multi_intent,
        primary_case_type="printer_connection_request",
        secondary_case_types=["printing_incident"],
        candidates=[
            CaseCandidate(case_type="printer_connection_request", case_type_version="1.0.0"),
            CaseCandidate(case_type="printing_incident", case_type_version="1.0.0"),
        ],
    )
    workflow, action_plan = _compiler().compile(frame=frame, decision=decision)

    assert workflow.state == WorkflowPlanState.needs_review
    assert workflow.disposition == Disposition.manual
    assert action_plan is None


def test_action_plan_hash_detects_parameter_change() -> None:
    frame = _frame(pc_name="WKS-01", connection_type="network", printer_address="10.20.30.40")
    _, plan = _compiler().compile(frame=frame, decision=_decision(frame, "printer_connection_request"))
    assert plan is not None

    changed_action = plan.actions[0].model_copy(update={"params": {**plan.actions[0].params, "pc_name": "WKS-02"}})
    assert compute_action_plan_hash(plan.model_copy(update={"actions": [changed_action]})) != plan.plan_hash
