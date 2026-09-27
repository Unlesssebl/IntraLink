"""ADR 0006 case, workflow and capability automation kernel."""

from core.automation.capabilities import CapabilityRegistry, CapabilitySpec
from core.automation.case_profiles import CaseProfileRegistry, CaseTypeProfile
from core.automation.case_router import CaseRouter
from core.automation.case_verifier import LLMCaseVerifier
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import (
    ActionPlan,
    ActionPlanState,
    ActionProposal,
    AssertionKind,
    CaseAssertion,
    CaseCandidate,
    CaseDecision,
    CaseDecisionState,
    CaseFrame,
    Disposition,
    ExtractionMethod,
    WorkflowPlan,
    WorkflowPlanState,
    WorkflowStep,
    WorkflowStepKind,
    compute_action_plan_hash,
)
from core.automation.frame_extractor import CaseFrameExtractor
from core.automation.persistence import AutomationBundle, AutomationRepository
from core.automation.preflight import ActionPreflightService
from core.automation.runner import RunnerAdvanceResult, RunnerState, WorkflowRunner
from core.automation.workflows import WorkflowDefinition, WorkflowRegistry

__all__ = [
    "ActionPlan",
    "ActionPlanState",
    "ActionPreflightService",
    "ActionProposal",
    "AssertionKind",
    "AutomationBundle",
    "AutomationRepository",
    "CaseAssertion",
    "CaseCandidate",
    "CaseDecision",
    "CaseDecisionState",
    "CaseFrame",
    "CaseFrameExtractor",
    "CaseProfileRegistry",
    "CaseRouter",
    "LLMCaseVerifier",
    "CaseTypeProfile",
    "CapabilityRegistry",
    "CapabilitySpec",
    "Disposition",
    "ExtractionMethod",
    "RunnerAdvanceResult",
    "RunnerState",
    "WorkflowPlan",
    "WorkflowPlanState",
    "WorkflowStep",
    "WorkflowStepKind",
    "WorkflowCompiler",
    "WorkflowDefinition",
    "WorkflowRegistry",
    "WorkflowRunner",
    "compute_action_plan_hash",
]
