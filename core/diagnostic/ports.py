"""Fast TCP port probing for workstation diagnostic (SMB:445, WinRM:5985, RPC:135)."""

import asyncio
from typing import Dict, Optional

TCP_PROBE_TIMEOUT_SEC: float = 0.3  # 300 ms


async def probe_tcp_port(host: Optional[str], port: int, timeout_sec: float = TCP_PROBE_TIMEOUT_SEC) -> bool:
    """Non-blocking TCP port reachability check with sub-second timeout."""
    if not host:
        return False
    try:
        conn = asyncio.open_connection(host, port)
        _, writer = await asyncio.wait_for(conn, timeout=timeout_sec)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def probe_diagnostic_ports(host: str, timeout_sec: float = TCP_PROBE_TIMEOUT_SEC) -> Dict[str, bool]:
    """Concurrent probe of standard Windows support ports (SMB 445 and WinRM 5985)."""
    smb_task = probe_tcp_port(host, 445, timeout_sec=timeout_sec)
    winrm_task = probe_tcp_port(host, 5985, timeout_sec=timeout_sec)

    smb_ok, winrm_ok = await asyncio.gather(smb_task, winrm_task)
    return {
        "smb_445": smb_ok,
        "winrm_5985": winrm_ok,
    }
