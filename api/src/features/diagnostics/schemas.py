"""Pydantic schemas for Diagnostics feature slice."""

from typing import Dict, Optional

from pydantic import BaseModel, Field


class HostDiagnosticDTO(BaseModel):
    hostname: str
    ip_address: Optional[str] = None
    is_online: bool
    avg_rtt: Optional[str] = None
    ports: Dict[str, bool] = Field(default_factory=dict)
    cached: bool = False


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
