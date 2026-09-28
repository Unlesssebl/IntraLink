"""HTTP contracts for the ADR 0006 automation engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from core.automation.contracts import ActionPlan, CaseDecision, CaseFrame, WorkflowPlan
from core.automation.service_routing import RedirectPlan, ServiceCompatibilityDecision


class TicketAutomationDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: int
    snapshot_hash: str
    case_frame: CaseFrame
    case_decision: CaseDecision
    service_compatibility: ServiceCompatibilityDecision
    workflow_plan: WorkflowPlan
    redirect_plan: RedirectPlan | None = None
    action_plan: ActionPlan | None = None
    approval: dict[str, Any] = Field(default_factory=dict)
    execution: list[dict[str, Any]] = Field(default_factory=list)


class ApproveActionPlanRequest(BaseModel):
    action_plan_id: UUID
    plan_hash: str = Field(min_length=64, max_length=64)
    snapshot_hash: str = Field(min_length=64, max_length=64)


class RedirectPlanRequest(BaseModel):
    redirect_plan_id: UUID
    plan_hash: str = Field(min_length=64, max_length=64)
    snapshot_hash: str = Field(min_length=64, max_length=64)
    version: int = Field(ge=1)
    reason_tag: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class CorrectRedirectPlanRequest(RedirectPlanRequest):
    target_service_id: int


class RedirectPlanResponse(BaseModel):
    status: str
    redirect_plan_id: UUID
    plan_hash: str
    version: int
    execution_state: str


class ActionPlanFeedbackRequest(BaseModel):
    action_plan_id: UUID
    plan_hash: str = Field(min_length=64, max_length=64)
    snapshot_hash: str = Field(min_length=64, max_length=64)
    reason_tag: str = Field(min_length=1, max_length=64)
    notes: str | None = None


class CorrectedActionRequest(BaseModel):
    capability_key: str = Field(min_length=2, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class CorrectActionPlanRequest(BaseModel):
    action_plan_id: UUID
    plan_hash: str = Field(min_length=64, max_length=64)
    snapshot_hash: str = Field(min_length=64, max_length=64)
    actions: list[CorrectedActionRequest] = Field(min_length=1, max_length=10)
    reason_tag: str = Field(min_length=1, max_length=64)
    notes: str | None = None


class CorrectCaseDecisionRequest(BaseModel):
    case_decision_id: UUID
    snapshot_hash: str = Field(min_length=64, max_length=64)
    corrected_case_type: str = Field(min_length=2, max_length=64)
    reason_tag: str = Field(min_length=1, max_length=64)
    notes: str | None = None


class CorrectTargetServiceRequest(BaseModel):
    compatibility_decision_id: UUID
    snapshot_hash: str = Field(min_length=64, max_length=64)
    target_service_id: int
    reason_tag: str = Field(default="operator_target_correction", min_length=1, max_length=64)
    notes: str | None = None


class ApprovalResponse(BaseModel):
    status: str
    action_plan_id: UUID
    command_id: UUID | None = None
    plan_hash: str


class FeedbackResponse(BaseModel):
    status: str
    feedback_id: UUID
    action_plan_id: UUID


class CatalogResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int


class AutomationCommandDTO(BaseModel):
    id: UUID
    task_id: int | None
    action_plan_id: UUID | None
    action_id: str | None
    capability_key: str | None
    sequence_no: int | None
    status: str
    initiator: str
    error_message: str | None
    result_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
