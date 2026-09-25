"""Base scenario interface and contracts for IntraLink v2 Autopilot."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from core.intraservice.service_definition import ServiceDefinition


class ExecutionAbortedException(Exception):
    """Raised when scenario execution is cooperatively aborted (e.g. human reclaim flag active)."""

    pass


class PreconditionResult(BaseModel):
    """Result of validating scenario prerequisites (data completeness and environment readiness)."""

    is_valid: bool = Field(..., description="True if all required facts and environmental conditions are met")
    missing_facts: List[str] = Field(default_factory=list, description="Missing entity keys (e.g. pc_name, printer_address)")
    environment_barriers: List[str] = Field(default_factory=list, description="Barriers detected (e.g. host_offline, port_unreachable)")
    clarification_prompt: Optional[str] = Field(default=None, description="Polite question or instruction for the applicant")


class ScenarioExecutionResult(BaseModel):
    """Result of autonomous scenario execution."""

    success: bool = Field(..., description="True if execution succeeded")
    action_taken: str = Field(..., description="Name of action executed")
    resolution_comment: str = Field(..., description="Public comment to post in the ticket for applicant")
    technical_note: str = Field(..., description="Hidden internal audit note for Helpdesk engineers (IsPrivateComment)")
    target_status_id: int = Field(default=3, description="Target IntraService status (3=Completed, 6=Paused, 2=In Progress)")
    error: Optional[str] = Field(default=None, description="Error message if execution failed")
    command_id: Optional[str] = Field(default=None, description="UUID of associated CommandRecord if dispatched")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Supplementary execution metrics and context")


class ScenarioMatch(BaseModel):
    """Evaluation result of scenario matching against a ticket."""

    scenario_key: str = Field(..., description="Key of the candidate scenario")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    matched: bool = Field(..., description="True if confidence >= threshold and no blocking barriers")
    reasons: List[str] = Field(default_factory=list, description="Positive contributing signals")
    barriers: List[str] = Field(default_factory=list, description="Negative or blocking signals")


class BaseScenario(ABC):
    """Abstract base class for all autopilot scenarios."""

    scenario_key: str
    name: str
    description: str
    semantic_prototypes: List[str] = []
    definition: Optional[ServiceDefinition] = None

    @abstractmethod
    async def can_handle(self, task: TaskDTO) -> bool:
        """Evaluate if this scenario matches the ticket."""
        ...

    async def evaluate_match(self, task: TaskDTO, semantic_score: float = 0.0) -> ScenarioMatch:
        """Evaluate detailed match confidence and reasons.

        Default implementation falls back to boolean can_handle or semantic similarity.
        """
        handled = await self.can_handle(task)
        if handled:
            return ScenarioMatch(
                scenario_key=self.scenario_key,
                confidence=0.95,
                matched=True,
                reasons=[f"Direct rule match for scenario '{self.scenario_key}'"],
                barriers=[],
            )

        # Semantic prototype fallback when keywords/regex missed but semantic affinity >= 0.75
        if semantic_score >= 0.75:
            return ScenarioMatch(
                scenario_key=self.scenario_key,
                confidence=semantic_score,
                matched=True,
                reasons=[
                    f"Semantic prototype match for '{self.scenario_key}' (cosine={semantic_score:.2f})"
                ],
                barriers=[],
            )

        return ScenarioMatch(
            scenario_key=self.scenario_key,
            confidence=0.0,
            matched=False,
            reasons=[],
            barriers=["can_handle returned false and semantic score below threshold"],
        )

    @abstractmethod
    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate whether all required facts exist and host/device is online."""
        ...

    @abstractmethod
    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Execute autonomous resolution actions."""
        ...
