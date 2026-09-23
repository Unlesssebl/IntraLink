"""Pydantic schemas for Tickets vertical slice."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TicketApplicantDTO(BaseModel):
    id: Optional[int] = None
    name: str = ""


class TicketSummaryDTO(BaseModel):
    id: int
    name: str
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    status_id: int
    status_name: str
    priority_name: Optional[str] = None
    created: Optional[str] = None
    applicant_name: Optional[str] = None
    pc_name: Optional[str] = None


class TicketDetailDTO(BaseModel):
    id: int
    name: str
    description: str
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    status_id: int
    status_name: str
    priority_name: Optional[str] = None
    created: Optional[str] = None
    creator_name: Optional[str] = None
    applicant_name: Optional[str] = None
    executor_ids: Optional[str] = None
    entities: Dict[str, Any] = Field(default_factory=dict)
    custom_fields: Dict[str, str] = Field(default_factory=dict)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


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
