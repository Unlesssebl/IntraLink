"""Typed contracts for scenario-driven ticket execution."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from shared.domain.models import DecisionOutcome, StrictModel


class FactState(str, Enum):
    MISSING = "missing"
    VALID = "valid"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"
    STALE = "stale"


class FactSource(str, Enum):
    STRUCTURED_FIELD = "structured_field"
    DIRECTORY = "directory"
    DIAGNOSTIC = "diagnostic"
    COMMENT = "comment"
    PARSER = "parser"
    LLM = "llm"
    OPERATOR = "operator"


class FactSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    SECRET = "secret"


class FactObservation(StrictModel):
    schema_version: Literal[1] = 1
    key: str = Field(min_length=1, max_length=100)
    value: Any = None
    state: FactState
    source: FactSource
    source_ref: str = Field(min_length=1, max_length=255)
    evidence_span: str | None = Field(default=None, max_length=2000)
    sensitivity: FactSensitivity = FactSensitivity.INTERNAL
    observed_at: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_grounding_for_values(self) -> "FactObservation":
        if self.state is FactState.VALID and self.value is None:
            raise ValueError("valid fact observation requires a value")
        if self.source in {FactSource.COMMENT, FactSource.PARSER, FactSource.LLM}:
            if self.value is not None and not (self.evidence_span or "").strip():
                raise ValueError("text-derived fact observation requires evidence_span")
        return self


class ResolvedFact(StrictModel):
    schema_version: Literal[1] = 1
    key: str
    value: Any = None
    state: FactState
    selected_source: FactSource | None = None
    selected_source_ref: str | None = None
    observations: list[FactObservation] = Field(default_factory=list)
    conflict_reason: str | None = None


class FactBag(StrictModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    facts: dict[str, ResolvedFact] = Field(default_factory=dict)

    def valid_value(self, key: str, default: Any = None) -> Any:
        fact = self.facts.get(key)
        if fact is None or fact.state is not FactState.VALID:
            return default
        return fact.value


class FactRequirement(StrictModel):
    schema_version: Literal[1] = 1
    key: str
    required: bool = True
    required_when: dict[str, Any] = Field(default_factory=dict)
    clarification_key: str | None = None


class ScenarioDefinition(StrictModel):
    schema_version: Literal[1] = 1
    key: str
    version: int = Field(ge=1)
    required_facts: list[FactRequirement] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    risk_level: Literal[0, 1, 2, 3] = 0
    clarification_outcome_key: str | None = None
    success_outcome_key: str | None = None


class ScenarioMatch(StrictModel):
    schema_version: Literal[1] = 1
    scenario_key: str
    scenario_version: int = Field(ge=1)
    matched: bool
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str | None = None


class PlanStep(StrictModel):
    id: str
    kind: Literal[
        "collect",
        "clarify",
        "decide",
        "approve",
        "dispatch",
        "wait",
        "finalize",
        "manual_review",
    ]
    requires: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(StrictModel):
    schema_version: Literal[1] = 1
    scenario_key: str
    scenario_version: int = Field(ge=1)
    fact_revision: int = Field(ge=0)
    steps: list[PlanStep] = Field(default_factory=list)


class CandidateOutcome(StrictModel):
    schema_version: Literal[1] = 1
    candidate_id: str
    source: Literal["rule", "rag", "diagnostic", "operator", "fallback"]
    outcome: DecisionOutcome
    evidence_refs: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    can_authorize_action: bool = False


class RejectedCandidate(StrictModel):
    candidate_id: str
    reason: str


class SynthesisProposal(StrictModel):
    schema_version: Literal[1] = 1
    selected_candidate_id: str
    used_evidence_refs: list[str] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    response_plan: dict[str, Any] = Field(default_factory=dict)


class DecisionResponse(StrictModel):
    schema_version: Literal[1] = 1
    text: str = Field(default="", max_length=900)
    mode: Literal["template", "llm", "fallback", "none"] = "none"
    state: Literal["valid", "fallback", "invalid"] = "invalid"
    violations: list[str] = Field(default_factory=list)
    used_evidence_refs: list[str] = Field(default_factory=list)


class DecisionGates(StrictModel):
    schema_version: Literal[1] = 1
    can_send_response: bool = False
    can_execute_action: bool = False
    requires_approval: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)


class DecisionEnvelope(StrictModel):
    schema_version: Literal[1] = 1
    decision_id: str
    decision_version: int = Field(default=1, ge=1)
    scenario_key: str
    scenario_version: int = Field(ge=1)
    facts_revision: int = Field(default=0, ge=0)
    analysis_state: Literal["succeeded", "manual_review", "system_error"] = "succeeded"
    facts_state: Literal["sufficient", "incomplete", "conflicting"] = "sufficient"
    facts_summary: dict[str, Any] = Field(default_factory=dict)
    candidates: list[CandidateOutcome] = Field(default_factory=list)
    outcome: DecisionOutcome
    policy: dict[str, Any] = Field(default_factory=dict)
    response: DecisionResponse = Field(default_factory=DecisionResponse)
    gates: DecisionGates = Field(default_factory=DecisionGates)
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: Literal[
        "proposed",
        "waiting_answer",
        "waiting_approval",
        "executing",
        "verified",
        "manual_review",
        "system_error",
    ] = "proposed"
