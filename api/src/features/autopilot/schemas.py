"""Autopilot API Schemas."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.autopilot.dto import AutopilotMode, AutopilotPolicyDTO


class AutopilotPoliciesListResponse(BaseModel):
    """Response wrapper for all scenario policies."""

    policies: List[AutopilotPolicyDTO]
    total: int


class UpdateAutopilotPolicyRequest(BaseModel):
    """Payload to update an autopilot scenario policy."""

    mode: AutopilotMode = Field(..., description="Target execution mode: FULL_AUTO, ASSISTED, or DISABLED")
    min_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Confidence threshold between 0.0 and 1.0")


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


class AgentPlanDTO(BaseModel):
    """Full supervisor blueprint for a ticket evaluated by the autonomous agent."""

    task_id: int
    scenario_key: str
    scenario_name: str
    description: str = ""
    confidence: float = 0.0
    matched: bool = False
    factor_breakdown: Dict[str, float] = Field(default_factory=dict)
    preconditions: Dict[str, Any] = Field(default_factory=dict)
    host_diagnostic: Optional[Dict[str, Any]] = None
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)
    candidate_hosts: List[str] = Field(default_factory=list)
    proposed_action: str = ""
    proposed_params: Dict[str, Any] = Field(default_factory=dict)
    suggested_comment: str = ""
    target_status_id: int = 3
    dialogue_state: Optional[Dict[str, Any]] = None
    command_id: Optional[uuid.UUID] = None
    last_event_id: Optional[int] = None
    is_circuit_broken: bool = False
    mode: str = "ASSISTED"


class ApprovePlanRequest(BaseModel):
    """Payload to approve agent plan without changes."""

    expected_status_id: Optional[int] = None
    override_comment: Optional[str] = None


class CorrectPlanRequest(BaseModel):
    """Payload to correct agent plan and contribute to the Ground-Truth dataset."""

    expected_status_id: Optional[int] = None
    corrected_scenario: str
    corrected_params: Dict[str, Any] = Field(default_factory=dict)
    corrected_comment: Optional[str] = None
    correction_tag: str = Field(
        default="general",
        description="Reason tag: typo, wrong_printer_model, slang, false_duplicate, policy_override",
    )
    operator_notes: Optional[str] = None


class AutopilotCorrectionDTO(BaseModel):
    """Historical correction record for harness coding agent."""

    id: uuid.UUID
    task_id: int
    original_scenario: str
    corrected_scenario: str
    original_params: Dict[str, Any]
    corrected_params: Dict[str, Any]
    original_comment: Optional[str] = None
    corrected_comment: Optional[str] = None
    confidence: float
    factors_snapshot: Dict[str, Any]
    correction_tag: str
    operator_notes: Optional[str] = None
    operator_username: str
    created_at: datetime


class CorrectionsListResponse(BaseModel):
    """Response wrapper for historical supervisor corrections."""

    corrections: List[AutopilotCorrectionDTO]
    total: int
