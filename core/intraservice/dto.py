"""Pydantic v2 Data Transfer Objects for IntraService domain entities."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExtractedEntitiesDTO(BaseModel):
    """Normalized workplace entities extracted from IntraService custom fields or body."""

    model_config = ConfigDict(populate_by_name=True)

    pc_name: str = ""
    phone: str = ""
    room: str = ""
    department: str = ""
    user_name: str = ""
    email: str = ""
    inventory_number: str = ""


class AttachmentDTO(BaseModel):
    """File attachment metadata in IntraService."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="Id")
    name: str = Field(alias="Name", default="")
    size: int = Field(alias="Size", default=0)


class TaskCommentDTO(BaseModel):
    """Comment in a ticket."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[int] = Field(default=None, alias="Id")
    text: str = Field(alias="Text", default="")
    created_at: Optional[datetime] = Field(default=None, alias="Created")
    author_name: str = Field(alias="AuthorName", default="")
    is_private: bool = Field(alias="IsPrivate", default=False)


class TaskDTO(BaseModel):
    """Normalized IntraService task representation."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int = Field(alias="Id")
    name: str = Field(alias="Name", default="")
    description: str = Field(alias="Description", default="")
    service_id: Optional[int] = Field(default=None, alias="ServiceId")
    service_name: Optional[str] = Field(default=None, alias="ServiceName")
    status_id: int = Field(alias="StatusId", default=0)
    status_name: str = Field(alias="StatusName", default="")
    priority_id: Optional[int] = Field(default=None, alias="PriorityId")
    priority_name: Optional[str] = Field(default=None, alias="PriorityName")
    task_type_id: Optional[int] = Field(default=None, alias="TaskTypeId")
    created: Optional[str] = Field(default=None, alias="Created")
    creator_id: Optional[int] = Field(default=None, alias="CreatorId")
    creator_name: Optional[str] = Field(default=None, alias="CreatorName")
    applicant_id: Optional[int] = Field(default=None, alias="ApplicantId")
    applicant_name: Optional[str] = Field(default=None, alias="ApplicantName")
    executor_ids: Optional[str] = Field(default=None, alias="ExecutorIds")

    # Raw XML and parsed custom fields
    custom_field_data: Optional[str] = Field(default=None, alias="CustomFieldData")
    entities: ExtractedEntitiesDTO = Field(default_factory=ExtractedEntitiesDTO)
    custom_fields: Dict[str, str] = Field(default_factory=dict)

    # Attachments
    attachments: List[AttachmentDTO] = Field(default_factory=list)


class TaskLifetimeEventDTO(BaseModel):
    """Audit lifetime event for an IntraService ticket."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int = Field(alias="Id")
    task_id: int = Field(alias="TaskId")
    created: Optional[str] = Field(default=None, alias="Created")
    user_name: Optional[str] = Field(default=None, alias="UserName")
    comment: Optional[str] = Field(default=None, alias="Comment")
    old_status_name: Optional[str] = Field(default=None, alias="OldStatusName")
    new_status_name: Optional[str] = Field(default=None, alias="NewStatusName")


class ServiceDTO(BaseModel):
    """IntraService catalog service item."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int = Field(alias="Id")
    name: str = Field(alias="Name")
    parent_id: Optional[int] = Field(default=None, alias="ParentId")
    is_active: bool = Field(default=True, alias="IsActive")
    description: Optional[str] = Field(default=None, alias="Description")


class TaskStatusDTO(BaseModel):
    """IntraService ticket status."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int = Field(alias="Id")
    name: str = Field(alias="Name")
    is_closed: bool = Field(default=False, alias="IsClosed")


class TaskTypeDTO(BaseModel):
    """IntraService Task Type schema."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int = Field(alias="Id")
    name: str = Field(alias="Name")
    service_id: int = Field(alias="ServiceId")
    fields: List[Dict[str, Any]] = Field(default_factory=list)
