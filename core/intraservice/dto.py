"""Pydantic v2 Data Transfer Objects for IntraService domain entities."""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


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
    printer_address: str = ""
    printer_model: str = ""
    target_user: str = ""
    first_name: str = ""
    last_name: str = ""
    middle_name: str = ""
    title: str = ""
    company: str = ""
    tab_number: str = ""
    similar_user: str = ""
    install_directum: str = ""
    directum_actions: str = ""
    it_login: str = ""
    it_password: str = ""
    directum_task_id: str = ""
    categories: str = ""
    applicant_company: str = ""
    applicant_login: str = ""
    applicant_ad_guid: str = ""

    @field_validator(
        "pc_name",
        "phone",
        "room",
        "department",
        "user_name",
        "email",
        "inventory_number",
        "printer_address",
        "printer_model",
        "target_user",
        "first_name",
        "last_name",
        "middle_name",
        "title",
        "company",
        "tab_number",
        "similar_user",
        "install_directum",
        "directum_actions",
        "it_login",
        "it_password",
        "directum_task_id",
        "categories",
        "applicant_company",
        "applicant_login",
        "applicant_ad_guid",
        mode="before",
    )
    @classmethod
    def coerce_entities_to_str(cls, v: Any) -> str:
        return "" if v is None else str(v)


class AttachmentDTO(BaseModel):
    """File attachment metadata in IntraService."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(alias="Id")
    name: str = Field(alias="Name", default="")
    size: int = Field(alias="Size", default=0)

    @field_validator("name", mode="before")
    @classmethod
    def coerce_name(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("size", mode="before")
    @classmethod
    def coerce_size(cls, v: Any) -> int:
        return 0 if v is None else int(v)


class TaskCommentDTO(BaseModel):
    """Comment in a ticket."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[int] = Field(default=None, alias="Id")
    text: str = Field(alias="Text", default="")
    created_at: Optional[datetime] = Field(default=None, alias="Created")
    author_name: str = Field(alias="AuthorName", default="")
    is_private: bool = Field(alias="IsPrivate", default=False)
    security_group_ids: Optional[Any] = Field(default=None, alias="TaskCommentSecurityGroupIDs")

    @model_validator(mode="before")
    @classmethod
    def normalize_privacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        is_priv = bool(data.get("is_private") or data.get("IsPrivate") or data.get("IsPrivateComment"))
        is_pub = data.get("IsPublic")
        if is_pub is None:
            is_pub = data.get("is_public")
        if is_pub is not None and (is_pub is False or str(is_pub).lower() in ("false", "0")):
            is_priv = True
        sec_groups = data.get("TaskCommentSecurityGroupIDs")
        if sec_groups is None:
            sec_groups = data.get("security_group_ids")
        if sec_groups is not None:
            if isinstance(sec_groups, (int, float)) and sec_groups != 0:
                is_priv = True
            elif isinstance(sec_groups, str):
                cleaned = [s.strip() for s in sec_groups.split(",") if s.strip()]
                if cleaned:
                    is_priv = True
            elif isinstance(sec_groups, (list, tuple, set)) and len(sec_groups) > 0:
                is_priv = True
        data["is_private"] = is_priv
        data["IsPrivate"] = is_priv
        return data

    @field_validator("text", "author_name", mode="before")
    @classmethod
    def coerce_comment_str(cls, v: Any) -> str:
        return "" if v is None else str(v)


class TaskCategoryDTO(BaseModel):
    """Normalized category identity (Id, Name)."""

    model_config = ConfigDict(populate_by_name=True)

    id: int = Field(default=0, validation_alias=AliasChoices("Id", "id"))
    name: str = Field(default="", validation_alias=AliasChoices("Name", "name"))

    @field_validator("id", mode="before")
    @classmethod
    def coerce_id(cls, v: Any) -> int:
        if v is None:
            return 0
        if isinstance(v, int):
            return v
        s = str(v).strip()
        return int(s) if s.isdigit() else 0

    @field_validator("name", mode="before")
    @classmethod
    def coerce_name(cls, v: Any) -> str:
        return "" if v is None else str(v).strip()


def normalize_category_pairs(
    categories: Any = None,
    category_ids: Any = None,
) -> List[TaskCategoryDTO]:
    """Parse categories and category_ids supporting || and comma delimiters into {id, name} pairs."""
    if not categories and not category_ids:
        return []

    # Parse IDs
    raw_ids: List[int] = []
    if isinstance(category_ids, (list, tuple)):
        for item in category_ids:
            if isinstance(item, int):
                raw_ids.append(item)
            elif str(item).strip().isdigit():
                raw_ids.append(int(str(item).strip()))
    elif category_ids is not None:
        s_ids = str(category_ids).strip()
        parts = [p.strip() for p in s_ids.split("||") if p.strip()] if "||" in s_ids else [p.strip() for p in s_ids.split(",") if p.strip()]
        for p in parts:
            if p.isdigit():
                raw_ids.append(int(p))

    # Parse Names
    raw_names: List[str] = []
    if isinstance(categories, (list, tuple)):
        raw_names = [str(item).strip() for item in categories if str(item).strip()]
    elif categories is not None:
        s_names = str(categories).strip()
        if "||" in s_names:
            raw_names = [p.strip() for p in s_names.split("||") if p.strip()]
        elif s_names:
            raw_names = [s_names]

    # Extract ID from "ID: Name" or "ID - Name" pattern in raw_names if ID is missing or 0
    extracted_names: List[str] = []
    for i, item in enumerate(raw_names):
        match = re.match(r"^(\d+)\s*[:\-]\s*(.+)$", item)
        if match:
            c_id = int(match.group(1))
            c_name = match.group(2).strip()
            if i < len(raw_ids):
                if raw_ids[i] == 0:
                    raw_ids[i] = c_id
            else:
                raw_ids.append(c_id)
            extracted_names.append(c_name)
        elif item.isdigit():
            c_id = int(item)
            if i < len(raw_ids):
                if raw_ids[i] == 0:
                    raw_ids[i] = c_id
            else:
                raw_ids.append(c_id)
            extracted_names.append("")
        else:
            extracted_names.append(item)

    raw_names = extracted_names

    # Pair them up
    count = max(len(raw_ids), len(raw_names))
    result: List[TaskCategoryDTO] = []
    for i in range(count):
        cid = raw_ids[i] if i < len(raw_ids) else 0
        cname = raw_names[i] if i < len(raw_names) else ""
        if cid or cname:
            result.append(TaskCategoryDTO(id=cid, name=cname))
    return result


class TaskDTO(BaseModel):
    """Normalized IntraService task representation."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def normalize_entities_key(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "Entities" in data and "entities" not in data:
                data["entities"] = data.pop("Entities")
            if "CustomFields" in data and "custom_fields" not in data:
                data["custom_fields"] = data.pop("CustomFields")
            # IntraService exposes the requester as Creator/CreatorId. Keep the
            # applicant projection separate from employee facts used by onboarding.
            if "Creator" in data and "CreatorName" not in data:
                data["CreatorName"] = data["Creator"]
            if "ApplicantName" not in data and data.get("Creator") is not None:
                data["ApplicantName"] = data["Creator"]
            if "ApplicantId" not in data and data.get("CreatorId") is not None:
                data["ApplicantId"] = data["CreatorId"]
            if "TypeId" in data and "TaskTypeId" not in data:
                data["TaskTypeId"] = data.get("TypeId")
            if "Data" in data and "CustomFieldData" not in data:
                data["CustomFieldData"] = data.get("Data")
        return data

    id: int = Field(alias="Id")
    name: str = Field(alias="Name", default="")
    description: str = Field(alias="Description", default="")
    service_id: Optional[int] = Field(default=None, alias="ServiceId")
    service_name: Optional[str] = Field(default=None, alias="ServiceName")
    status_id: int = Field(alias="StatusId", default=0)
    status_name: str = Field(alias="StatusName", default="")
    priority_id: Optional[int] = Field(default=None, alias="PriorityId")
    priority_name: Optional[str] = Field(default=None, alias="PriorityName")
    task_type_id: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("TaskTypeId", "TypeId", "task_type_id"),
    )
    created: Optional[str] = Field(default=None, alias="Created")
    changed: Optional[str] = Field(default=None, alias="Changed")

    @field_validator("name", "description", "status_name", mode="before")
    @classmethod
    def coerce_to_str(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("status_id", mode="before")
    @classmethod
    def coerce_to_int(cls, v: Any) -> int:
        return 0 if v is None else int(v)

    creator_id: Optional[int] = Field(default=None, alias="CreatorId")
    creator_name: Optional[str] = Field(default=None, alias="CreatorName")
    applicant_id: Optional[int] = Field(default=None, alias="ApplicantId")
    applicant_name: Optional[str] = Field(default=None, alias="ApplicantName")
    executor_ids: Optional[str] = Field(default=None, alias="ExecutorIds")

    categories: Optional[str] = Field(default=None, alias="Categories")
    category_ids: Optional[str] = Field(default=None, alias="CategoryIds")
    creator_company_id: Optional[int] = Field(default=None, alias="CreatorCompanyId")
    creator_company_name: Optional[str] = Field(default=None, alias="CreatorCompanyName")
    creator_company_path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("CreatorCompanyPath", "creator_company_path"),
    )
    creator_department: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("CreatorDepartment", "creator_department", "ApplicantDepartment", "applicant_department"),
    )
    creator_login: Optional[str] = Field(default=None, alias="CreatorLogin")
    creator_ad_guid: Optional[str] = Field(default=None, alias="CreatorADGuid")
    creator_position: Optional[str] = Field(default=None, alias="CreatorPosition")
    company_id: Optional[int] = Field(default=None, alias="CompanyId")
    company_name: Optional[str] = Field(default=None, alias="CompanyName")
    normalized_categories: List[TaskCategoryDTO] = Field(default_factory=list)

    @model_validator(mode="after")
    def populate_normalized_categories(self) -> "TaskDTO":
        if not self.normalized_categories and (self.categories or self.category_ids):
            self.normalized_categories = normalize_category_pairs(self.categories, self.category_ids)
        return self

    def get_categories(self) -> List[TaskCategoryDTO]:
        """Return parsed category pairs."""
        return self.normalized_categories

    # Raw XML and parsed custom fields
    custom_field_data: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("CustomFieldData", "Data", "custom_field_data"),
    )
    entities: ExtractedEntitiesDTO = Field(default_factory=ExtractedEntitiesDTO)
    custom_fields: Dict[str, str] = Field(default_factory=dict)

    # Attachments
    attachments: List[AttachmentDTO] = Field(default_factory=list, alias="Attachments")

    def get_executor_ids(self) -> List[int]:
        """Parse comma-separated ExecutorIds string into a list of integer IDs."""
        if not self.executor_ids:
            return []
        result = []
        for part in str(self.executor_ids).split(","):
            part = part.strip()
            if part.isdigit():
                result.append(int(part))
        return result


class TaskLifetimeEventDTO(BaseModel):
    """Audit lifetime event for an IntraService ticket."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[int] = Field(default=None, alias="Id")
    event_key: Optional[str] = Field(default=None, alias="EventKey")
    task_id: Optional[int] = Field(default=None, alias="TaskId")
    created: Optional[str] = Field(default=None, alias="Created")
    user_name: Optional[str] = Field(default=None, alias="UserName")
    editor: Optional[str] = Field(default=None, alias="Editor")
    editor_id: Optional[int] = Field(default=None, alias="EditorId")
    status_id: Optional[int] = Field(default=None, alias="StatusId")
    executors: Optional[str] = Field(default=None, alias="Executors")
    executors_group: Optional[str] = Field(default=None, alias="ExecutorsGroup")
    comment: Optional[str] = Field(default=None, alias="Comment")
    old_status_name: Optional[str] = Field(default=None, alias="OldStatusName")
    new_status_name: Optional[str] = Field(default=None, alias="NewStatusName")
    is_private: bool = Field(default=False, alias="IsPrivateComment")
    security_group_ids: Optional[Any] = Field(default=None, alias="TaskCommentSecurityGroupIDs")

    @model_validator(mode="before")
    @classmethod
    def normalize_privacy(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "Comments" in data and "Comment" not in data:
            data["Comment"] = data["Comments"]
        if "Date" in data and "Created" not in data:
            data["Created"] = data["Date"]
        if "Editor" in data and "UserName" not in data:
            data["UserName"] = data["Editor"]
        is_priv = bool(data.get("is_private") or data.get("IsPrivate") or data.get("IsPrivateComment"))
        is_pub = data.get("IsPublic")
        if is_pub is None:
            is_pub = data.get("is_public")
        if is_pub is not None and (is_pub is False or str(is_pub).lower() in ("false", "0")):
            is_priv = True
        sec_groups = data.get("TaskCommentSecurityGroupIDs")
        if sec_groups is None:
            sec_groups = data.get("security_group_ids")
        if sec_groups is not None:
            if isinstance(sec_groups, (int, float)) and sec_groups != 0:
                is_priv = True
            elif isinstance(sec_groups, str):
                cleaned = [s.strip() for s in sec_groups.split(",") if s.strip()]
                if cleaned:
                    is_priv = True
            elif isinstance(sec_groups, (list, tuple, set)) and len(sec_groups) > 0:
                is_priv = True
        data["is_private"] = is_priv
        data["IsPrivateComment"] = is_priv
        return data

    @field_validator(
        "created",
        "user_name",
        "editor",
        "comment",
        "old_status_name",
        "new_status_name",
        "executors",
        "executors_group",
        mode="before",
    )
    @classmethod
    def coerce_lifetime_str(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("id", "task_id", mode="before")
    @classmethod
    def coerce_lifetime_int(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0


class ServiceDTO(BaseModel):
    """IntraService catalog service item."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int = Field(alias="Id")
    name: str = Field(alias="Name")
    parent_id: Optional[int] = Field(default=None, alias="ParentId")
    is_active: bool = Field(default=True, alias="IsActive")
    description: Optional[str] = Field(default=None, alias="Description")
    task_type_id: Optional[int] = Field(default=None, alias="TaskTypeId")
    form_metadata: Dict[str, Any] = Field(default_factory=dict, alias="FormMetadata")
    fields: List[Dict[str, Any]] = Field(default_factory=list, alias="Fields")


class TaskStatusDTO(BaseModel):
    """IntraService ticket status."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int = Field(alias="Id")
    name: str = Field(alias="Name")
    is_final: bool = Field(default=False, alias="IsFinal")
    is_fixed: bool = Field(default=False, alias="IsFixed")
    is_initial: bool = Field(default=False, alias="IsInitial")
    is_comment_required: bool = Field(default=False, alias="IsCommentRequired")

    @field_validator("is_final", "is_fixed", "is_initial", "is_comment_required", mode="before")
    @classmethod
    def coerce_nullable_flags(cls, value: Any) -> Any:
        return False if value is None else value


class TaskTypeDTO(BaseModel):
    """IntraService Task Type schema."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int = Field(alias="Id")
    name: str = Field(alias="Name")
    service_id: int = Field(alias="ServiceId")
    fields: List[Dict[str, Any]] = Field(default_factory=list)
