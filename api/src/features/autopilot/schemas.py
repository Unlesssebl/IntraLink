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
