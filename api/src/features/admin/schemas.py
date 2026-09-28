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
