"""Host diagnostics service orchestrating low-level network probes and Redis caching."""

import asyncio
import json
import logging
from typing import Dict, Optional

import redis.asyncio as aioredis
from pydantic import BaseModel, Field

from core.diagnostic.ping import fast_ping, resolve_dns_fast
from core.diagnostic.ports import probe_diagnostic_ports, probe_tcp_port

logger = logging.getLogger("core.diagnostic.service")
DIAG_CACHE_TTL = 600  # 10 minutes


class HostDiagnosticDTO(BaseModel):
    hostname: str
    ip_address: Optional[str] = None
    is_online: bool
    avg_rtt: Optional[str] = None
    ports: Dict[str, bool] = Field(default_factory=dict)
    cached: bool = False


class HostDiagnosticsService:
    """Service providing fast host reachability checks and port diagnostics."""

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

    async def probe_port(self, host: str, port: int, timeout_sec: float = 0.3) -> bool:
        return await probe_tcp_port(host=host, port=port, timeout_sec=timeout_sec)
