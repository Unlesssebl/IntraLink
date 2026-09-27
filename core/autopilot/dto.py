"""Autopilot Domain Transfer Objects (DTOs) and Routing Contracts."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

AutopilotMode = Literal["FULL_AUTO", "ASSISTED", "DISABLED"]
AnalysisState = Literal["not_analyzed", "in_progress", "ready", "stale", "failed"]
RoutingStateLiteral = Literal[
    "selected",
    "needs_clarification",
    "ambiguous",
    "unmatched",
    "degraded",
    "refused",
]
ApprovalState = Literal[
    "not_applicable",
    "ready_for_approval",
    "approved",
    "corrected",
    "rejected",
    "manual_takeover",
    "blocked",
]


class AutopilotPolicyDTO(BaseModel):
    """Pydantic v2 DTO representing an autopilot scenario policy."""

    model_config = ConfigDict(from_attributes=True)

    scenario_key: str = Field(..., description="Unique scenario identifier (e.g. install_printer)")
    mode: AutopilotMode = Field(default="ASSISTED", description="FULL_AUTO, ASSISTED, or DISABLED")
    consecutive_failures: int = Field(default=0, ge=0, description="Consecutive failure count for circuit breaker")
    last_failure_at: Optional[datetime] = Field(default=None, description="Timestamp of the most recent failure")
    is_circuit_broken: bool = Field(default=False, description="True if circuit breaker tripped to ASSISTED")
    description: Optional[str] = Field(default=None, description="Human-readable description of scenario")
    created_at: Optional[datetime] = Field(default=None, description="Policy creation timestamp")
    updated_at: Optional[datetime] = Field(default=None, description="Policy last updated timestamp")


class AutopilotPolicyUpdateDTO(BaseModel):
    """Request payload for updating an autopilot scenario policy."""

    mode: AutopilotMode = Field(..., description="New mode: FULL_AUTO, ASSISTED, or DISABLED")


class PreflightCheckDTO(BaseModel):
    """Individual preflight check item status and diagnostic payload."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., description="Name of the check (e.g. host_reachability, ad_user_exists)")
    status: str = Field(..., description="Status: passed, failed, warning, skipped")
    details: Dict[str, Any] = Field(default_factory=dict, description="Detailed check payload")
    message: Optional[str] = Field(default=None, description="Human-readable status summary")


class PreflightResultDTO(BaseModel):
    """Result of read-only environmental and identity preflight verification."""

    model_config = ConfigDict(from_attributes=True)

    id: Optional[uuid.UUID] = Field(default=None, description="Preflight record UUID")
    status: str = Field(default="passed", description="Overall preflight status: passed, failed, degraded, not_applicable")
    scenario_key: str = Field(..., description="Target scenario key")
    params_hash: str = Field(default="", description="Canonical SHA-256 hash of parameters")
    checks: List[PreflightCheckDTO] = Field(default_factory=list, description="List of individual checks")
    details: Dict[str, Any] = Field(default_factory=dict, description="Diagnostics metadata")
    error_message: Optional[str] = Field(default=None, description="Error message if preflight failed")
    expires_at: Optional[datetime] = Field(default=None, description="Timestamp when preflight expires (120s TTL)")
    created_at: Optional[datetime] = Field(default=None, description="Preflight execution timestamp")
    is_expired: bool = Field(default=False, description="True if preflight has expired")


class EvidenceSummaryDTO(BaseModel):
    """Sanitized operator-facing evidence summary."""

    model_config = ConfigDict(from_attributes=True)

    source_type: str = Field(..., description="Source kind: catalog, lexical, semantic, verifier, readiness")
    reason_code: str = Field(..., description="Canonical reason code")
    description: str = Field(..., description="Short human-readable safe description")
    scenario_key: Optional[str] = Field(default=None, description="Scenario associated with this evidence")
    verdict_polarity: str = Field(default="supporting", description="Polarity: supporting, contradicting, neutral")
    provider: str = Field(default="", description="Provider identifier")
    version: Optional[str] = Field(default=None, description="Component version")
    is_degraded: bool = Field(default=False, description="True if provider had degraded health")


class ScenarioFieldSchemaDTO(BaseModel):
    """Schema definition for an operator-editable scenario parameter."""

    model_config = ConfigDict(from_attributes=True)

    field_key: str = Field(..., description="Parameter field key (e.g. pc_name, target_user)")
    label: str = Field(..., description="Human-readable field label")
    field_type: str = Field(default="string", description="Field type: string, integer, boolean, select")
    required: bool = Field(default=False, description="Whether field is strictly required")
    default_value: Optional[Any] = Field(default=None, description="Default fallback value")
    options: Optional[List[Dict[str, str]]] = Field(default=None, description="Select options [{value, label}]")
    hint: Optional[str] = Field(default=None, description="Safe placeholder or explanation hint")


class ScenarioCatalogItemDTO(BaseModel):
    """Scenario available for operator selection and correction."""

    model_config = ConfigDict(from_attributes=True)

    scenario_key: str = Field(..., description="Unique scenario identifier")
    name: str = Field(..., description="Human-readable scenario name")
    description: str = Field(..., description="Detailed description of what the scenario does")
    policy_mode: AutopilotMode = Field(default="ASSISTED", description="Scenario execution mode")
    is_enabled: bool = Field(default=True, description="True if scenario can be selected for correction")
    required_facts: List[str] = Field(default_factory=list, description="List of required facts")
    editable_fields: List[ScenarioFieldSchemaDTO] = Field(default_factory=list, description="Operator-editable fields")
    supported_executor: str = Field(default="intralink_worker", description="Target executor capability")
    disabled_reason: Optional[str] = Field(default=None, description="Reason if scenario is disabled")


class RoutingFeedbackDTO(BaseModel):
    """DTO representing human supervisor feedback with complete provenance."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    decision_id: Optional[uuid.UUID] = None
    prepared_plan_id: Optional[uuid.UUID] = None
    command_id: Optional[uuid.UUID] = None
    task_id: int
    snapshot_hash: Optional[str] = None
    operator_username: str = "operator"
    verdict: str = Field(..., description="approved, corrected, rejected, or manual_takeover")
    original_scenario: Optional[str] = None
    corrected_scenario: Optional[str] = None
    original_params: Dict[str, Any] = Field(default_factory=dict)
    corrected_params: Dict[str, Any] = Field(default_factory=dict)
    reason_tag: Optional[str] = None
    operator_notes: Optional[str] = None
    router_version: Optional[str] = None
    prompt_version: Optional[str] = None
    verifier_used: Optional[bool] = False
    source: str = "runtime"
    created_at: Optional[datetime] = None


class RoutingQualityMetricsDTO(BaseModel):
    """Aggregated routing quality and calibration metrics."""

    model_config = ConfigDict(from_attributes=True)

    total_decisions: int = 0
    by_routing_state: Dict[str, int] = Field(default_factory=dict)
    approve_rate: float = 0.0
    correction_rate: float = 0.0
    reject_takeover_rate: float = 0.0
    by_scenario: Dict[str, int] = Field(default_factory=dict)
    scenario_transitions: List[Dict[str, Any]] = Field(default_factory=list)
    llm_verifier_call_rate: float = 0.0
    verifier_agreement_rate: Optional[float] = None
    degraded_provider_rate: float = 0.0
    missing_facts_rate: float = 0.0
    preflight_failure_rate: float = 0.0
    command_success_rate: float = 0.0
    command_failure_rate: float = 0.0
    p50_latency_ms: Optional[float] = None
    p95_latency_ms: Optional[float] = None


class AgentPlanDTO(BaseModel):
    """Full blueprint for a ticket evaluated by the autonomous agent with server-projected state."""

    model_config = ConfigDict(from_attributes=True)

    task_id: int
    scenario_key: str
    scenario_name: str
    description: str = ""
    analysis_state: AnalysisState = "ready"
    routing_state: RoutingStateLiteral = "selected"
    approval_state: ApprovalState = "not_applicable"
    can_approve: bool = False
    can_correct: bool = True
    can_reject: bool = True
    blocking_reason_codes: List[str] = Field(default_factory=list)
    is_stale: bool = False
    freshness_expires_at: Optional[datetime] = None
    has_terminal_feedback: bool = False
    command_id: Optional[uuid.UUID] = None
    command_status: Optional[str] = None
    decision_id: Optional[uuid.UUID] = None
    snapshot_hash: str = ""
    plan_id: Optional[uuid.UUID] = None
    plan_hash: str = ""
    decision_reason_codes: List[str] = Field(default_factory=list)
    degraded_components: Dict[str, str] = Field(default_factory=dict)
    missing_facts: List[str] = Field(default_factory=list)
    preflight: Optional[PreflightResultDTO] = None
    is_executable: bool = False
    evidence_summaries: List[EvidenceSummaryDTO] = Field(default_factory=list)
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)
    candidate_hosts: List[str] = Field(default_factory=list)
    proposed_action: str = ""
    proposed_params: Dict[str, Any] = Field(default_factory=dict)
    suggested_comment: str = ""
    target_status_id: int = 3
    last_event_id: Optional[int] = None
    is_circuit_broken: bool = False
    mode: str = "ASSISTED"
    is_tense: bool = False
    tense_reason: Optional[str] = None
    has_attachments: bool = False
