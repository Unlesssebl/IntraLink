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
from core.automation.service_routing import (
    ServiceCompatibilityDecision,
    ServiceCompatibilityState,
    ServiceRouteBinding,
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
        compatibility: ServiceCompatibilityDecision | None = None,
        binding: ServiceRouteBinding | None = None,
    ) -> tuple[WorkflowPlan, ActionPlan | None]:
        if decision.state not in (CaseDecisionState.selected, CaseDecisionState.multi_intent):
            raise ValueError("Only selected case decisions can be compiled")
        if decision.state == CaseDecisionState.multi_intent:
            return self._manual_multi_intent(frame, decision, clarification_round)

        case_type = decision.primary_case_type or ""
        definition = self.workflows.for_case_type(case_type)
        if definition is None:
            return (
                WorkflowPlan(
                    task_id=frame.task_id,
                    snapshot_hash=frame.snapshot_hash,
                    case_decision_id=decision.id,
                    workflow_key="unsupported_workflow",
                    workflow_version="1.0.0",
                    state=WorkflowPlanState.unsupported,
                    disposition=Disposition.manual,
                    reason_codes=["no_registered_workflow"],
                ),
                None,
            )

        if frame.conflicting_facts:
            workflow = self._workflow_plan(
                frame,
                decision,
                definition,
                state=WorkflowPlanState.needs_review,
                disposition=Disposition.manual,
                clarification_round=clarification_round,
                reason_codes=["conflicting_facts"],
            )
            return workflow, None

        if compatibility is not None and compatibility.state != ServiceCompatibilityState.compatible:
            workflow = self._workflow_plan(
                frame,
                decision,
                definition,
                state=WorkflowPlanState.needs_review,
                disposition=Disposition.manual,
                clarification_round=clarification_round,
                reason_codes=[*compatibility.reason_codes, "service_not_compatible"],
            )
            return workflow, None

        facts = self._facts(frame)
        required_facts = list(definition.required_facts)
        if binding is not None:
            required_facts.extend(item for item in binding.required_fields if item not in required_facts)
        missing = [name for name in required_facts if not str(facts.get(name, "")).strip()]
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
        if capability_key == "create_ad_user":
            if not self._ad_authorized(decision, definition, compatibility, binding):
                workflow = self._workflow_plan(
                    frame,
                    decision,
                    definition,
                    state=WorkflowPlanState.needs_review,
                    disposition=Disposition.manual,
                    clarification_round=clarification_round,
                    reason_codes=["ad_service_not_authorized"],
                )
                return workflow, None
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
            state=(
                WorkflowPlanState.ready_for_approval
                if definition.key == "employee_onboarding_workflow"
                else WorkflowPlanState.awaiting_approval
            ),
            disposition=Disposition.execute,
            clarification_round=clarification_round,
        )
        action_id = f"action_{capability_key}_0"
        action = ActionProposal(
            id=action_id,
            capability_key=capability_key,
            sequence_no=0,
            params={
                key: facts[key] for key in (*capability.required_params, *capability.optional_params) if key in facts
            },
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
            source_service_id=compatibility.source_service_id if compatibility else None,
            service_binding_key=binding.key if binding else None,
            service_binding_version=binding.version if binding else None,
            catalog_hash=compatibility.catalog_hash if compatibility else None,
            state=ActionPlanState.ready,
            disposition=Disposition.execute,
            actions=[action],
        )
        action_plan = draft.model_copy(update={"plan_hash": compute_action_plan_hash(draft)})
        return workflow, action_plan

    @staticmethod
    def _ad_authorized(
        decision: CaseDecision,
        definition: WorkflowDefinition,
        compatibility: ServiceCompatibilityDecision | None,
        binding: ServiceRouteBinding | None,
    ) -> bool:
        return bool(
            compatibility
            and compatibility.state == ServiceCompatibilityState.compatible
            and binding
            and binding.is_active
            and binding.is_validated
            and binding.catalog_hash == compatibility.catalog_hash
            and binding.key == "ad_account_creation"
            and decision.primary_case_type == "employee_onboarding"
            and "employee_onboarding" in binding.allowed_case_types
            and definition.key == "employee_onboarding_workflow"
            and definition.key in binding.allowed_workflows
            and "create_ad_user" in binding.allowed_capabilities
            and compatibility.source_service_id in binding.service_ids
            and (
                binding.required_task_type_id is None
                or compatibility.source_task_type_id == binding.required_task_type_id
            )
        )

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
