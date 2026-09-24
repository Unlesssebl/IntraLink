"""Pydantic schemas for Triage feature slice."""

import uuid
from typing import List, Optional

from pydantic import BaseModel, Field


class TriageDecisionDTO(BaseModel):
    action: str  # 'auto_classify', 'cancel_duplicate', 'redirect_service', 'manual_resolve'
    target_service_id: Optional[int] = None
    target_service_name: Optional[str] = None
    confidence: float
    reason: str
    suggested_comment: Optional[str] = None
    suggested_status_id: Optional[int] = None
    is_duplicate: bool = False
    master_ticket_id: Optional[int] = None


class TriageAnalysisResponse(BaseModel):
    audit_id: uuid.UUID
    ticket_id: int
    decision: TriageDecisionDTO
    model_used: str
    rule_matched: Optional[str] = None


class BatchTriageRequest(BaseModel):
    filter_id: int = 984
    limit: int = Field(default=20, ge=1, le=100)


class BatchTriageResponse(BaseModel):
    total_analyzed: int
    decisions: List[TriageAnalysisResponse]


class ApplyDecisionRequest(BaseModel):
    override_comment: Optional[str] = None
    override_status_id: Optional[int] = None
