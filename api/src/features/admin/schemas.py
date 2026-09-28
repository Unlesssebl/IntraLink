"""Contracts for the administrator credentials vault page."""

from datetime import datetime

from pydantic import BaseModel, Field


class ServiceCredentialsUpdate(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=512)
    sync_catalog: bool = True


class CatalogStatus(BaseModel):
    available: bool
    version: int | None = None
    entries: int = 0
    active_bindings: int = 0


class ServiceCredentialsStatus(BaseModel):
    configured: bool
    encryption_ready: bool
    login: str | None = None
    bot_user_id: int | None = None
    updated_at: datetime | None = None
    redis_cached: bool = False
    catalog: CatalogStatus


class ServiceCredentialsUpdateResponse(BaseModel):
    status: ServiceCredentialsStatus
    catalog_sync: str
    catalog_sync_error: str | None = None


class AdAccountBindingStatus(BaseModel):
    binding_key: str = "ad_account_creation"
    service_id: int = 53
    service_path: str | None = None
    catalog_hash: str | None = None
    catalog_version: int | None = None
    task_type_id: int | None = None
    field_metadata: list[dict] = Field(default_factory=list)
    required_facts: list[str] = Field(default_factory=lambda: ["last_name", "first_name", "department", "title"])
    workflow_key: str = "employee_onboarding_workflow"
    capability_key: str = "create_ad_user"
    active: bool = False
    validated: bool = False
    requires_confirmation: bool = True


class ActivateAdAccountBindingRequest(BaseModel):
    catalog_hash: str = Field(min_length=64, max_length=64)
    confirmed_service_path: str = Field(min_length=1, max_length=1000)
