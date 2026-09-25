"""Diagnostics business logic and network probes orchestration."""

import asyncio
import json
import logging
from typing import Optional

import redis.asyncio as aioredis

from api.src.core.config import settings
from core.diagnostic import fast_ping, probe_diagnostic_ports, probe_tcp_port, resolve_dns_fast
from core.diagnostic.service import HostDiagnosticDTO, HostDiagnosticsService
from core.intraservice import IntraServiceClient

from .schemas import (
    PortProbeResponse,
    TicketDiagnosticDTO,
)

logger = logging.getLogger("api.features.diagnostics")
DIAG_CACHE_TTL = 600  # 10 minutes


class DiagnosticsService(HostDiagnosticsService):
    """Service providing fast host reachability checks and port diagnostics."""

    def __init__(self, intraservice_client: Optional[IntraServiceClient] = None) -> None:
        self.intraservice_client = intraservice_client or IntraServiceClient(
            base_url=settings.INTRASERVICE_URL,
            verify_ssl=settings.SSL_VERIFY,
        )

    async def diagnose_host(
        self,
        hostname: str,
        redis_client: Optional[aioredis.Redis] = None,
    ) -> HostDiagnosticDTO:
        clean_host = hostname.strip().upper()

        # 1. Check Redis cache
        cache_key = f"diag:host:{clean_host}"
        if redis_client:
            try:
                cached = await redis_client.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    data["cached"] = True
                    return HostDiagnosticDTO.model_validate(data)
            except Exception as exc:
                logger.debug(f"Redis cache read error: {exc}")

        # 2. DNS resolve
        ip = await resolve_dns_fast(clean_host)
        target = ip or clean_host

        # 3. Concurrent Ping & Port Probes
        ping_task = fast_ping(target)
        ports_task = probe_diagnostic_ports(target)

        ping_res, ports_res = await asyncio.gather(ping_task, ports_task)

        dto = HostDiagnosticDTO(
            hostname=clean_host,
            ip_address=ip,
            is_online=ping_res.get("is_online", False),
            avg_rtt=ping_res.get("avg_rtt"),
            ports=ports_res,
            cached=False,
        )

        # 4. Save to Redis
        if redis_client:
            try:
                await redis_client.set(cache_key, dto.model_dump_json(), ex=DIAG_CACHE_TTL)
            except Exception as exc:
                logger.debug(f"Redis cache write error: {exc}")

        return dto

    async def diagnose_ticket(
        self,
        ticket_id: int,
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
    ) -> TicketDiagnosticDTO:
        task = await self.intraservice_client.get_task(task_id=ticket_id, auth_b64=auth_b64)
        pc_name = task.entities.pc_name if task.entities else None

        if not pc_name:
            return TicketDiagnosticDTO(
                ticket_id=ticket_id,
                pc_name=None,
                diagnostic=None,
                message="В полях заявки не найдено имя рабочей станции (ПК).",
            )

        # Take primary PC if comma separated
        primary_pc = pc_name.split(",")[0].strip()
        diag = await self.diagnose_host(hostname=primary_pc, redis_client=redis_client)

        return TicketDiagnosticDTO(
            ticket_id=ticket_id,
            pc_name=primary_pc,
            diagnostic=diag,
            message="Диагностика успешно выполнена.",
        )

    async def probe_port(self, host: str, port: int, timeout_sec: float = 0.3) -> PortProbeResponse:
        is_open = await probe_tcp_port(host=host, port=port, timeout_sec=timeout_sec)
        return PortProbeResponse(host=host, port=port, is_open=is_open)
