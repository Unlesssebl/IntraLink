"""Immutable domain contracts introduced by ADR 0006."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

MACHINE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotComment(FrozenModel):
    """Sanitized public comment included in an immutable ticket snapshot."""

    id: int | None = None
    text: str
    created_at: str | None = None
    author_name: str
    is_private: bool = False


class SnapshotAttachment(FrozenModel):
    """Attachment metadata only; attachment content never enters automation intake."""

    id: int
    name: str
    size: int


class TicketSnapshot(FrozenModel):
    """Canonical sanitized point-in-time view used by every automation stage."""

    task_id: int
    status_id: int
    service_id: int | None = None
    service_name: str | None = None
    title: str
    description: str
    public_comments: list[SnapshotComment] = Field(default_factory=list)
    custom_fields: dict[str, str] = Field(default_factory=dict)
    entities: dict[str, str] = Field(default_factory=dict)
    attachments: list[SnapshotAttachment] = Field(default_factory=list)
    last_event_id: int | None = None
    snapshot_hash: str = Field(min_length=64, max_length=64)


class AssertionKind(str, Enum):
    domain = "domain"
    intent = "intent"
    symptom = "symptom"
    requested_outcome = "requested_outcome"
    entity = "entity"
    constraint = "constraint"
    negation = "negation"


class ExtractionMethod(str, Enum):
    deterministic = "deterministic"
    llm = "llm"
    operator = "operator"


class CaseDecisionState(str, Enum):
    selected = "selected"
    multi_intent = "multi_intent"
    ambiguous = "ambiguous"
    unknown = "unknown"
    degraded = "degraded"


class Disposition(str, Enum):
    execute = "execute"
    clarify = "clarify"
    consult = "consult"
    redirect = "redirect"
    manual = "manual"


class WorkflowPlanState(str, Enum):
    awaiting_facts = "awaiting_facts"
    awaiting_diagnostics = "awaiting_diagnostics"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    running = "running"
    needs_review = "needs_review"
    completed = "completed"
    cancelled = "cancelled"


class WorkflowStepKind(str, Enum):
    clarification = "clarification"
    diagnostic = "diagnostic"
    action = "action"
    disposition = "disposition"


class ActionPlanState(str, Enum):
    draft = "draft"
    ready = "ready"
    approved = "approved"
    running = "running"
    needs_review = "needs_review"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class CaseAssertion(FrozenModel):
    id: str
    kind: AssertionKind
    key: str
    value: str
    source_ref: str
    text_span: str | None = None
    extraction_method: ExtractionMethod
    is_negated: bool = False

    @model_validator(mode="after")
    def validate_assertion(self) -> "CaseAssertion":
        if not MACHINE_KEY_RE.fullmatch(self.key):
            raise ValueError("CaseAssertion.key must be a lowercase machine key")
        if self.extraction_method == ExtractionMethod.llm and not self.text_span:
            raise ValueError("LLM assertions require a literal text_span")
        return self


class CaseFrame(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: int
    snapshot_hash: str = Field(min_length=64, max_length=64)
    frame_version: str
    assertions: list[CaseAssertion] = Field(default_factory=list)
    entities: dict[str, str] = Field(default_factory=dict)
    unknown_facts: list[str] = Field(default_factory=list)
    conflicting_facts: list[str] = Field(default_factory=list)
    degraded_components: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_frame(self) -> "CaseFrame":
        assertion_ids = [item.id for item in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("CaseFrame assertion IDs must be unique")
        for key in (*self.unknown_facts, *self.conflicting_facts):
            if not MACHINE_KEY_RE.fullmatch(key):
                raise ValueError("Fact keys must be lowercase machine keys")
        return self


class CaseEvidence(FrozenModel):
    id: str
    candidate_key: str
    source: str
    polarity: str
    strength: str
    source_ref: str
    text_span: str | None = None


class CaseCandidate(FrozenModel):
    case_type: str
    case_type_version: str
    evidence_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_key(self) -> "CaseCandidate":
        if not MACHINE_KEY_RE.fullmatch(self.case_type):
            raise ValueError("case_type must be a lowercase machine key")
        if set(self.evidence_ids) & set(self.contradiction_ids):
            raise ValueError("Evidence cannot support and contradict the same candidate")
        return self


class CaseDecision(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: int
    snapshot_hash: str = Field(min_length=64, max_length=64)
    frame_id: UUID
    router_version: str
    prompt_version: str | None = None
    state: CaseDecisionState
    primary_case_type: str | None = None
    secondary_case_types: list[str] = Field(default_factory=list)
    candidates: list[CaseCandidate] = Field(default_factory=list)
    evidence: list[CaseEvidence] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    degradation_reason: str | None = None
    verifier_trace: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_decision(self) -> "CaseDecision":
        selected_states = {CaseDecisionState.selected, CaseDecisionState.multi_intent}
        if self.state in selected_states and not self.primary_case_type:
            raise ValueError("Selected case decision requires primary_case_type")
        if self.state not in selected_states and self.primary_case_type is not None:
            raise ValueError("Non-selected case decision cannot expose primary_case_type")
        if self.state == CaseDecisionState.multi_intent and not self.secondary_case_types:
            raise ValueError("multi_intent requires secondary_case_types")
        if self.state != CaseDecisionState.multi_intent and self.secondary_case_types:
            raise ValueError("secondary_case_types are only valid for multi_intent")
        if self.state == CaseDecisionState.degraded and not self.degradation_reason:
            raise ValueError("degraded decision requires degradation_reason")
        if self.state != CaseDecisionState.degraded and self.degradation_reason is not None:
            raise ValueError("degradation_reason is only valid for degraded decisions")
        candidate_keys = {candidate.case_type for candidate in self.candidates}
        chosen = {key for key in [self.primary_case_type, *self.secondary_case_types] if key}
        if not chosen.issubset(candidate_keys):
            raise ValueError("Selected case types must be present among candidates")
        evidence_ids = {item.id for item in self.evidence}
        for candidate in self.candidates:
            if not set(candidate.evidence_ids + candidate.contradiction_ids).issubset(evidence_ids):
                raise ValueError("Candidate references unknown evidence")
        return self


class WorkflowStep(FrozenModel):
    id: str
    kind: WorkflowStepKind
    key: str
    depends_on: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    required_facts: list[str] = Field(default_factory=list)
    is_mutating: bool = False


class WorkflowPlan(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: int
    snapshot_hash: str = Field(min_length=64, max_length=64)
    case_decision_id: UUID
    workflow_key: str
    workflow_version: str
    state: WorkflowPlanState
    disposition: Disposition
    steps: list[WorkflowStep] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    clarification_round: int = Field(default=0, ge=0, le=2)
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_workflow(self) -> "WorkflowPlan":
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Workflow step IDs must be unique")
        known = set(step_ids)
        for step in self.steps:
            if not set(step.depends_on).issubset(known):
                raise ValueError("Workflow step dependency is unknown")
            if step.id in step.depends_on:
                raise ValueError("Workflow step cannot depend on itself")
        if self.state == WorkflowPlanState.awaiting_facts and not self.missing_facts:
            raise ValueError("awaiting_facts requires missing_facts")
        if self.state != WorkflowPlanState.awaiting_facts and self.missing_facts:
            raise ValueError("missing_facts only belongs to awaiting_facts")
        return self


class ActionProposal(FrozenModel):
    id: str
    capability_key: str
    sequence_no: int = Field(ge=0)
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on_action_ids: list[str] = Field(default_factory=list)
    risk: str = "medium"
    requires_approval: bool = True


class ActionPlan(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: int
    snapshot_hash: str = Field(min_length=64, max_length=64)
    case_decision_id: UUID
    workflow_plan_id: UUID
    workflow_key: str
    workflow_version: str
    state: ActionPlanState
    disposition: Disposition
    actions: list[ActionProposal] = Field(default_factory=list)
    plan_hash: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_action_plan(self) -> "ActionPlan":
        ids = [action.id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ValueError("Action IDs must be unique")
        if len({action.sequence_no for action in self.actions}) != len(self.actions):
            raise ValueError("Action sequence numbers must be unique")
        known = set(ids)
        for action in self.actions:
            if not set(action.depends_on_action_ids).issubset(known):
                raise ValueError("Action dependency is unknown")
            if action.id in action.depends_on_action_ids:
                raise ValueError("Action cannot depend on itself")
        if self.disposition == Disposition.execute and not self.actions:
            raise ValueError("execute disposition requires at least one action")
        if self.disposition != Disposition.execute and self.actions:
            raise ValueError("Non-execute disposition cannot contain mutating actions")
        expected_hash = compute_action_plan_hash(self)
        if self.plan_hash and self.plan_hash != expected_hash:
            raise ValueError("ActionPlan plan_hash is not canonical")
        return self


def compute_action_plan_hash(plan: ActionPlan | dict[str, Any]) -> str:
    """Hash every execution-relevant binding in an ActionPlan."""
    data = plan.model_dump(mode="json") if isinstance(plan, ActionPlan) else dict(plan)
    canonical = {
        "id": str(data.get("id")),
        "task_id": data.get("task_id"),
        "snapshot_hash": data.get("snapshot_hash"),
        "case_decision_id": str(data.get("case_decision_id")),
        "workflow_plan_id": str(data.get("workflow_plan_id")),
        "workflow_key": data.get("workflow_key"),
        "workflow_version": data.get("workflow_version"),
        "disposition": data.get("disposition"),
        "actions": data.get("actions", []),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
