"""Diagnostics feature router."""

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends

from api.src.core.redis import get_redis

from .schemas import (
    HostDiagnosticDTO,
    PortProbeRequest,
    PortProbeResponse,
    TicketDiagnosticDTO,
)
from .service import DiagnosticsService

router = APIRouter(prefix="/diagnostics", tags=["Diagnostics"])


def get_diagnostics_service() -> DiagnosticsService:
    return DiagnosticsService()


@router.get("/host/{hostname}", response_model=HostDiagnosticDTO)
async def check_host(
    hostname: str,
    service: DiagnosticsService = Depends(get_diagnostics_service),
    redis: aioredis.Redis = Depends(get_redis),
) -> HostDiagnosticDTO:
    """Run express network diagnostics on a workstation (Ping, SMB:445, WinRM:5985)."""
    return await service.diagnose_host(hostname=hostname, redis_client=redis)


@router.get("/ticket/{ticket_id}", response_model=TicketDiagnosticDTO)
async def check_ticket_workstation(
    ticket_id: int,
    service: DiagnosticsService = Depends(get_diagnostics_service),
    redis: aioredis.Redis = Depends(get_redis),
) -> TicketDiagnosticDTO:
    """Extract PC hostname from ticket and execute express reachability diagnostics."""
    return await service.diagnose_ticket(ticket_id=ticket_id, redis_client=redis)


@router.post("/probe", response_model=PortProbeResponse)
async def probe_port(
    req: PortProbeRequest,
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> PortProbeResponse:
    """Execute fast non-blocking TCP port probe."""
    return await service.probe_port(host=req.host, port=req.port, timeout_sec=req.timeout_sec)
