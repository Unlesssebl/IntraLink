"""Pydantic schemas for Tickets vertical slice."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class TicketApplicantDTO(BaseModel):
    id: Optional[int] = None
    name: str = ""


class TicketSummaryDTO(BaseModel):
    id: int
    name: str = ""
    description: str = ""
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    status_id: int = 0
    status_name: str = ""
    priority_name: Optional[str] = None
    created: Optional[str] = None
    applicant_name: Optional[str] = None
    pc_name: Optional[str] = None
    is_tense: bool = False
    tense_reason: Optional[str] = None
    has_attachments: bool = False
    scenario_key: Optional[str] = None
    scenario_name: Optional[str] = None
    confidence: Optional[float] = None

    @field_validator("name", "description", "status_name", mode="before")
    @classmethod
    def coerce_summary_str(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("status_id", mode="before")
    @classmethod
    def coerce_summary_int(cls, v: Any) -> int:
        return 0 if v is None else int(v)


class TicketDetailDTO(BaseModel):
    id: int
    name: str = ""
    description: str = ""
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    status_id: int = 0
    status_name: str = ""
    priority_name: Optional[str] = None
    created: Optional[str] = None
    creator_name: Optional[str] = None
    applicant_name: Optional[str] = None
    executor_ids: Optional[str] = None
    entities: Dict[str, Any] = Field(default_factory=dict)
    custom_fields: Dict[str, str] = Field(default_factory=dict)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("name", "description", "status_name", mode="before")
    @classmethod
    def coerce_detail_str(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("status_id", mode="before")
    @classmethod
    def coerce_detail_int(cls, v: Any) -> int:
        return 0 if v is None else int(v)


class UpdateTicketRequest(BaseModel):
    status_id: Optional[int] = None
    comment: Optional[str] = None
    executor_ids: Optional[str] = None
    is_private: bool = False


class AddCommentRequest(BaseModel):
    comment: str
    is_private: bool = False


class ExecuteTicketActionRequest(BaseModel):
    action: str  # e.g., 'cancel_duplicate', 'redirect_service', 'assign_engineer'
    params: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = None


class CommandActionResponse(BaseModel):
    command_id: uuid.UUID
    idempotency_key: str
    action: str
    status: str
    created_at: datetime


class CommandStatusResponse(BaseModel):
    command_id: uuid.UUID
    idempotency_key: str
    action: str
    status: str
    initiator: str
    task_id: Optional[int] = None
    target_json: Dict[str, Any] = Field(default_factory=dict)
    params_json: Dict[str, Any] = Field(default_factory=dict)
    result_json: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
