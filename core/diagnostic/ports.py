"""Fast non-blocking TCP socket probing for workstation diagnostics (SMB:445, WinRM:5985, RAW:9100, RPC:135)."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

TCP_PROBE_TIMEOUT_SEC: float = 0.3  # 300 ms legacy default
FAST_PROBE_TIMEOUT_SEC: float = 1.5  # 1.5s architecture specification standard


@dataclass
class FastProbeResult:
    """Diagnostic outcome of FastSocketProbe check."""

    host: str
    is_online: bool
    ports: Dict[int, bool] = field(default_factory=dict)
    rtt_ms: Optional[float] = None
    error: Optional[str] = None


async def probe_tcp_port(
    host: Optional[str],
    port: int,
    timeout_sec: float = TCP_PROBE_TIMEOUT_SEC,
) -> bool:
    """Non-blocking TCP port reachability check with sub-second timeout."""
    if not host or not host.strip():
        return False
    try:
        conn = asyncio.open_connection(host.strip(), port)
        _, writer = await asyncio.wait_for(conn, timeout=timeout_sec)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def probe_diagnostic_ports(
    host: str,
    timeout_sec: float = TCP_PROBE_TIMEOUT_SEC,
) -> Dict[str, bool]:
    """Concurrent probe of standard Windows support ports (SMB 445 and WinRM 5985)."""
    smb_task = probe_tcp_port(host, 445, timeout_sec=timeout_sec)
    winrm_task = probe_tcp_port(host, 5985, timeout_sec=timeout_sec)

    smb_ok, winrm_ok = await asyncio.gather(smb_task, winrm_task)
    return {
        "smb_445": smb_ok,
        "winrm_5985": winrm_ok,
    }


class FastSocketProbe:
    """High-performance non-blocking socket probe adhering to 1.5s lifecycle timeout.

    Performs concurrent TCP handshake probes across standard support ports
    (WinRM: 5985, Printer RAW: 9100, SMB: 445, RPC: 135) to determine workstation
    and peripheral reachability without hanging worker pools.
    """

    def __init__(self, default_timeout_sec: float = FAST_PROBE_TIMEOUT_SEC) -> None:
        self.default_timeout_sec = default_timeout_sec

    async def probe(
        self,
        host: Optional[str],
        ports: Optional[List[int]] = None,
        timeout_sec: Optional[float] = None,
    ) -> FastProbeResult:
        """Probe host reachability across specified TCP ports concurrently."""
        clean_host = (host or "").strip()
        if not clean_host:
            return FastProbeResult(
                host="",
                is_online=False,
                ports={},
                error="Имя хоста или IP не указаны",
            )

        timeout = timeout_sec or self.default_timeout_sec
        target_ports = ports if ports is not None else [5985, 445]
        if not target_ports:
            target_ports = [5985]

        start_time = time.monotonic()

        tasks = [
            probe_tcp_port(clean_host, port, timeout_sec=timeout)
            for port in target_ports
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        port_map: Dict[int, bool] = {}
        for port, res in zip(target_ports, results):
            if isinstance(res, bool):
                port_map[port] = res
            else:
                port_map[port] = False

        is_online = any(port_map.values())
        elapsed_ms = round((time.monotonic() - start_time) * 1000.0, 1)

        return FastProbeResult(
            host=clean_host,
            is_online=is_online,
            ports=port_map,
            rtt_ms=elapsed_ms if is_online else None,
        )
