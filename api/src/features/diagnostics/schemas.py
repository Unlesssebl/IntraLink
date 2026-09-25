"""Pydantic schemas for Diagnostics feature slice."""

from typing import Optional

from pydantic import BaseModel, Field


from core.diagnostic.service import HostDiagnosticDTO


class TicketDiagnosticDTO(BaseModel):
    ticket_id: int
    pc_name: Optional[str] = None
    diagnostic: Optional[HostDiagnosticDTO] = None
    message: Optional[str] = None


class PortProbeRequest(BaseModel):
    host: str
    port: int = Field(..., ge=1, le=65535)
    timeout_sec: float = Field(default=0.3, ge=0.1, le=5.0)


class PortProbeResponse(BaseModel):
    host: str
    port: int
    is_open: bool
