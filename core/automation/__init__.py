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
from core.automation.execution_policy import (
    POLICY_ENGINE_VERSION,
    ExecutionPhase,
    ExecutionPolicyContext,
    ExecutionPolicyDecision,
    ExecutionPolicyEvaluator,
    compute_context_hash,
    compute_decision_hash,
)
from core.automation.frame_extractor import CaseFrameExtractor
from core.automation.persistence import AutomationBundle, AutomationRepository
from core.automation.policy import CapabilityHealthService
from core.automation.preflight import ActionPreflightService
from core.automation.resolution import (
    HumanActionConflictError,
    HumanActionNotFoundError,
    HumanActionResolutionError,
    HumanActionResolutionService,
    HumanActionValidationError,
)
from core.automation.runner import RunnerAdvanceResult, RunnerState, WorkflowRunner
from core.automation.worker_attestation import (
    WorkerAttestationResult,
    WorkerAttestationService,
    WorkerHeartbeat,
)
from core.automation.workflows import (
    FactNormalization,
    WorkflowDefinition,
    WorkflowFactSpec,
    WorkflowRegistry,
)

__all__ = [
    "FactNormalization",
    "WorkflowFactSpec",
    "ActionPlan",
    "ActionPlanState",
    "ActionPreflightService",
    "ActionProposal",
    "AssertionKind",
    "AutomationBundle",
    "AutomationRepository",
    "CapabilityHealthService",
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
    "ExecutionPhase",
    "ExecutionPolicyContext",
    "ExecutionPolicyDecision",
    "ExecutionPolicyEvaluator",
    "ExtractionMethod",
    "HumanActionConflictError",
    "HumanActionNotFoundError",
    "HumanActionResolutionError",
    "HumanActionResolutionService",
    "HumanActionValidationError",
    "POLICY_ENGINE_VERSION",
    "RunnerAdvanceResult",
    "RunnerState",
    "WorkerAttestationResult",
    "WorkerAttestationService",
    "WorkerHeartbeat",
    "WorkflowPlan",
    "WorkflowPlanState",
    "WorkflowStep",
    "WorkflowStepKind",
    "WorkflowCompiler",
    "WorkflowDefinition",
    "WorkflowRegistry",
    "WorkflowRunner",
    "compute_action_plan_hash",
    "compute_context_hash",
    "compute_decision_hash",
]
