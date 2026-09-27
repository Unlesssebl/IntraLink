"""Autopilot API Schemas and Request/Response Contracts."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.autopilot.dto import (
    AgentPlanDTO,
    AutopilotMode,
    AutopilotPolicyDTO,
    RoutingFeedbackDTO,
    RoutingQualityMetricsDTO,
    ScenarioCatalogItemDTO,
)


class AutopilotPoliciesListResponse(BaseModel):
    """Response wrapper for all scenario policies."""

    policies: List[AutopilotPolicyDTO]
    total: int


class UpdateAutopilotPolicyRequest(BaseModel):
    """Payload to update an autopilot scenario policy."""

    mode: AutopilotMode = Field(..., description="Target execution mode: FULL_AUTO, ASSISTED, or DISABLED")


class AutopilotCommandDTO(BaseModel):
    """DTO for CommandRecord live feed entry."""

    id: uuid.UUID
    action: str
    status: str
    task_id: Optional[int] = None
    initiator: str
    target_json: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AutopilotStatsResponse(BaseModel):
    """Autopilot efficiency and impact metrics."""

    total_automated_actions: int
    hours_saved: float
    active_scenarios_count: int
    tripped_circuit_breakers: int


class ApprovePlanRequest(BaseModel):
    """Payload to approve prepared agent plan without changes."""

    decision_id: uuid.UUID = Field(..., description="ID of the RoutingDecision being approved")
    plan_id: uuid.UUID = Field(..., description="Prepared plan UUID")
    plan_hash: str = Field(..., min_length=64, max_length=64, description="Canonical SHA-256 hash of the prepared plan")
    snapshot_hash: str = Field(..., min_length=64, max_length=64, description="Canonical SHA-256 hash of the ticket snapshot")
    expected_status_id: int = Field(..., description="Expected IntraService status ID for OCC validation")
    last_event_id: Optional[int] = Field(..., description="Expected last lifetime event ID watermark (null if no events)")


class CorrectPlanRequest(BaseModel):
    """Payload to correct agent plan and record human supervision feedback."""

    decision_id: uuid.UUID = Field(..., description="ID of the RoutingDecision being corrected")
    plan_id: uuid.UUID = Field(..., description="Prepared plan UUID")
    plan_hash: str = Field(..., min_length=64, max_length=64, description="Canonical SHA-256 hash of the prepared plan")
    snapshot_hash: str = Field(..., min_length=64, max_length=64, description="Canonical SHA-256 hash of the ticket snapshot")
    expected_status_id: int = Field(..., description="Expected IntraService status ID for OCC validation")
    last_event_id: Optional[int] = Field(..., description="Expected last lifetime event ID watermark (null if no events)")
    corrected_scenario: str = Field(..., min_length=1, description="Target corrected scenario key")
    corrected_params: Dict[str, Any] = Field(default_factory=dict, description="Corrected parameters")
    corrected_comment: Optional[str] = None
    correction_tag: str = Field(
        ...,
        min_length=1,
        description="Mandatory reason tag: typo, wrong_scenario, missing_parameters, policy_override, applicant_changed_request, other",
    )
    operator_notes: Optional[str] = None


class RejectPlanRequest(BaseModel):
    """Payload to reject an agent proposal without creating an execution command."""

    decision_id: uuid.UUID = Field(..., description="ID of the RoutingDecision being rejected")
    plan_id: uuid.UUID = Field(..., description="Prepared plan UUID")
    plan_hash: str = Field(..., min_length=64, max_length=64, description="Canonical SHA-256 hash of the prepared plan")
    snapshot_hash: str = Field(..., min_length=64, max_length=64, description="Canonical SHA-256 hash of the ticket snapshot")
    reason_tag: str = Field(default="rejected", description="Rejection reason code")
    operator_notes: Optional[str] = Field(default=None, description="Operator explanation notes")


class ManualTakeoverRequest(BaseModel):
    """Payload to leave the ticket for human engineer takeover without running automation."""

    decision_id: uuid.UUID = Field(..., description="ID of the RoutingDecision being assigned to human")
    plan_id: uuid.UUID = Field(..., description="Prepared plan UUID")
    plan_hash: str = Field(..., min_length=64, max_length=64, description="Canonical SHA-256 hash of the prepared plan")
    snapshot_hash: str = Field(..., min_length=64, max_length=64, description="Canonical SHA-256 hash of the ticket snapshot")
    reason_tag: str = Field(default="manual_takeover", description="Takeover reason code")
    operator_notes: Optional[str] = Field(default=None, description="Operator explanation notes")


class FeedbackListResponse(BaseModel):
    """Response wrapper for historical operator feedback and corrections."""

    feedback: List[RoutingFeedbackDTO]
    total: int


class ScenarioCatalogResponse(BaseModel):
    """Response wrapper for executable scenario catalog."""

    scenarios: List[ScenarioCatalogItemDTO]
    total: int


class RoutingQualityMetricsResponse(BaseModel):
    """Response wrapper for routing calibration and quality metrics."""

    metrics: RoutingQualityMetricsDTO


class ApprovePlanResponse(BaseModel):
    """Response returned upon approving a plan."""

    status: str = "approved"
    command_id: str
    ticket_id: int
    action: str
    plan_hash: str
    is_duplicate: bool = False


class CorrectPlanResponse(BaseModel):
    """Response returned upon correcting a plan."""

    status: str = "corrected"
    command_id: str
    ticket_id: int
    action: str
    plan_id: str
    plan_hash: str
    is_duplicate: bool = False


class RejectPlanResponse(BaseModel):
    """Response returned upon rejecting a plan."""

    status: str = "rejected"
    feedback_id: str
    ticket_id: int
    is_duplicate: bool = False


class ManualTakeoverResponse(BaseModel):
    """Response returned upon taking over a ticket manually."""

    status: str = "manual_takeover"
    feedback_id: str
    ticket_id: int
    is_duplicate: bool = False
    external_update_succeeded: Optional[bool] = Field(
        default=None,
        description="Whether the IntraService status/note update was confirmed; null for an idempotent replay",
    )
    warning: Optional[str] = None


class BatchAssignRequest(BaseModel):
    """Payload to batch assign tickets to autopilot service bot."""

    ticket_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="List of ticket IDs to assign to bot (1..50)",
    )


class BatchAssignResponse(BaseModel):
    """Result of batch assignment to autopilot bot."""

    assigned_count: int
    failed_ids: List[int]
    details: Dict[int, str]


__all__ = [
    "AgentPlanDTO",
    "ApprovePlanRequest",
    "ApprovePlanResponse",
    "AutopilotCommandDTO",
    "AutopilotMode",
    "AutopilotPoliciesListResponse",
    "AutopilotPolicyDTO",
    "AutopilotStatsResponse",
    "BatchAssignRequest",
    "BatchAssignResponse",
    "CorrectPlanRequest",
    "CorrectPlanResponse",
    "FeedbackListResponse",
    "ManualTakeoverRequest",
    "ManualTakeoverResponse",
    "RejectPlanRequest",
    "RejectPlanResponse",
    "RoutingFeedbackDTO",
    "RoutingQualityMetricsDTO",
    "RoutingQualityMetricsResponse",
    "ScenarioCatalogItemDTO",
    "ScenarioCatalogResponse",
    "UpdateAutopilotPolicyRequest",
]
