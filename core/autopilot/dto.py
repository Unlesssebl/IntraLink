import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

AutopilotMode = Literal["FULL_AUTO", "ASSISTED", "DISABLED"]


class AutopilotPolicyDTO(BaseModel):
    """Pydantic v2 DTO representing an autopilot scenario policy."""

    model_config = ConfigDict(from_attributes=True)

    scenario_key: str = Field(..., description="Unique scenario identifier (e.g. install_printer)")
    mode: AutopilotMode = Field(default="ASSISTED", description="FULL_AUTO, ASSISTED, or DISABLED")
    min_confidence: float = Field(default=0.85, ge=0.0, le=1.0, description="Minimum confidence score threshold")
    consecutive_failures: int = Field(default=0, ge=0, description="Consecutive failure count for circuit breaker")
    last_failure_at: Optional[datetime] = Field(default=None, description="Timestamp of the most recent failure")
    is_circuit_broken: bool = Field(default=False, description="True if circuit breaker tripped to ASSISTED")
    description: Optional[str] = Field(default=None, description="Human-readable description of scenario")
    created_at: Optional[datetime] = Field(default=None, description="Policy creation timestamp")
    updated_at: Optional[datetime] = Field(default=None, description="Policy last updated timestamp")


class AutopilotPolicyUpdateDTO(BaseModel):
    """Request payload for updating an autopilot scenario policy."""

    mode: AutopilotMode = Field(..., description="New mode: FULL_AUTO, ASSISTED, or DISABLED")
    min_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="New confidence threshold")


class AgentPlanDTO(BaseModel):
    """Full blueprint for a ticket evaluated by the autonomous agent."""

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
    is_tense: bool = False
    tense_reason: Optional[str] = None
    has_attachments: bool = False
