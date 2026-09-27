"""Deterministic compiler from CaseDecision to WorkflowPlan and ActionPlan."""

from __future__ import annotations

from uuid import uuid4

from core.automation.capabilities import CapabilityRegistry
from core.automation.contracts import (
    ActionPlan,
    ActionPlanState,
    ActionProposal,
    AssertionKind,
    CaseDecision,
    CaseDecisionState,
    CaseFrame,
    Disposition,
    WorkflowPlan,
    WorkflowPlanState,
    WorkflowStep,
    WorkflowStepKind,
    compute_action_plan_hash,
)
from core.automation.workflows import WorkflowDefinition, WorkflowRegistry


class WorkflowCompiler:
    def __init__(self, workflows: WorkflowRegistry, capabilities: CapabilityRegistry) -> None:
        self.workflows = workflows
        self.capabilities = capabilities

    def compile(
        self,
        *,
        frame: CaseFrame,
        decision: CaseDecision,
        clarification_round: int = 0,
    ) -> tuple[WorkflowPlan, ActionPlan | None]:
        if decision.state not in (CaseDecisionState.selected, CaseDecisionState.multi_intent):
            raise ValueError("Only selected case decisions can be compiled")
        if decision.state == CaseDecisionState.multi_intent:
            return self._manual_multi_intent(frame, decision, clarification_round)

        case_type = decision.primary_case_type or ""
        definition = self.workflows.for_case_type(case_type)
        if definition is None:
            raise ValueError(f"No workflow registered for case type '{case_type}'")

        facts = self._facts(frame)
        missing = [name for name in definition.required_facts if not str(facts.get(name, "")).strip()]
        if missing:
            if clarification_round >= definition.max_clarification_rounds:
                workflow = self._workflow_plan(
                    frame,
                    decision,
                    definition,
                    state=WorkflowPlanState.needs_review,
                    disposition=Disposition.manual,
                    clarification_round=clarification_round,
                    reason_codes=["clarification_limit_reached"],
                )
                return workflow, None
            workflow = self._workflow_plan(
                frame,
                decision,
                definition,
                state=WorkflowPlanState.awaiting_facts,
                disposition=Disposition.clarify,
                missing_facts=missing,
                clarification_round=clarification_round,
                reason_codes=["required_facts_missing"],
            )
            return workflow, None

        capability_key = self._select_capability(definition, frame)
        if capability_key is None:
            state = (
                WorkflowPlanState.awaiting_diagnostics
                if definition.diagnostic_capabilities
                else WorkflowPlanState.awaiting_approval
            )
            workflow = self._workflow_plan(
                frame,
                decision,
                definition,
                state=state,
                disposition=definition.default_disposition,
                clarification_round=clarification_round,
                reason_codes=["diagnosis_required"] if definition.diagnostic_capabilities else [],
            )
            return workflow, None

        capability = self.capabilities.require(capability_key)
        missing_action_params = self.capabilities.validate_params(capability_key, facts)
        if missing_action_params:
            workflow = self._workflow_plan(
                frame,
                decision,
                definition,
                state=WorkflowPlanState.awaiting_facts,
                disposition=Disposition.clarify,
                missing_facts=missing_action_params,
                clarification_round=clarification_round,
                reason_codes=["capability_params_missing"],
            )
            return workflow, None

        workflow = self._workflow_plan(
            frame,
            decision,
            definition,
            state=WorkflowPlanState.awaiting_approval,
            disposition=Disposition.execute,
            clarification_round=clarification_round,
        )
        action_id = f"action_{capability_key}_0"
        action = ActionProposal(
            id=action_id,
            capability_key=capability_key,
            sequence_no=0,
            params={key: facts[key] for key in (*capability.required_params, *capability.optional_params) if key in facts},
            risk=capability.risk.value,
            requires_approval=True,
        )
        draft = ActionPlan(
            id=uuid4(),
            task_id=frame.task_id,
            snapshot_hash=frame.snapshot_hash,
            case_decision_id=decision.id,
            workflow_plan_id=workflow.id,
            workflow_key=definition.key,
            workflow_version=definition.version,
            state=ActionPlanState.ready,
            disposition=Disposition.execute,
            actions=[action],
        )
        action_plan = draft.model_copy(update={"plan_hash": compute_action_plan_hash(draft)})
        return workflow, action_plan

    @staticmethod
    def _facts(frame: CaseFrame) -> dict[str, str]:
        facts = dict(frame.entities)
        for assertion in frame.assertions:
            if assertion.kind == AssertionKind.entity and not assertion.is_negated:
                facts.setdefault(assertion.key, assertion.value)
        return facts

    @staticmethod
    def _select_capability(definition: WorkflowDefinition, frame: CaseFrame) -> str | None:
        if len(definition.allowed_capabilities) == 1:
            return definition.allowed_capabilities[0]
        intent_keys = {
            assertion.key
            for assertion in frame.assertions
            if assertion.kind in (AssertionKind.intent, AssertionKind.symptom) and not assertion.is_negated
        }
        if definition.key == "printing_incident_workflow":
            if intent_keys & {"wrong_default_printer", "set_default_printer"}:
                return "set_default_printer"
            if intent_keys & {"stuck_print_queue", "spooler_failure", "clear_print_queue"}:
                return "reset_print_spooler"
        return None

    @staticmethod
    def _workflow_plan(
        frame: CaseFrame,
        decision: CaseDecision,
        definition: WorkflowDefinition,
        *,
        state: WorkflowPlanState,
        disposition: Disposition,
        missing_facts: list[str] | None = None,
        clarification_round: int = 0,
        reason_codes: list[str] | None = None,
    ) -> WorkflowPlan:
        steps: list[WorkflowStep] = []
        for index, key in enumerate(definition.diagnostic_capabilities):
            steps.append(
                WorkflowStep(
                    id=f"diagnostic_{index}_{key}",
                    kind=WorkflowStepKind.diagnostic,
                    key=key,
                    is_mutating=False,
                )
            )
        return WorkflowPlan(
            task_id=frame.task_id,
            snapshot_hash=frame.snapshot_hash,
            case_decision_id=decision.id,
            workflow_key=definition.key,
            workflow_version=definition.version,
            state=state,
            disposition=disposition,
            steps=steps,
            missing_facts=missing_facts or [],
            clarification_round=clarification_round,
            reason_codes=reason_codes or [],
        )

    def _manual_multi_intent(
        self,
        frame: CaseFrame,
        decision: CaseDecision,
        clarification_round: int,
    ) -> tuple[WorkflowPlan, None]:
        workflow = WorkflowPlan(
            task_id=frame.task_id,
            snapshot_hash=frame.snapshot_hash,
            case_decision_id=decision.id,
            workflow_key="multi_intent_review_workflow",
            workflow_version="1.0.0",
            state=WorkflowPlanState.needs_review,
            disposition=Disposition.manual,
            clarification_round=clarification_round,
            reason_codes=["multi_intent_requires_operator_composition"],
        )
        return workflow, None
