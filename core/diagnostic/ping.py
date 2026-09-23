"""Fast asynchronous ICMP Ping and DNS resolution probes."""

import asyncio
import os
import re
import socket
import subprocess
from typing import Any, Dict, Optional

PING_TIMEOUT_SEC: float = 0.4
DOMAIN_SUFFIX = ".corporate.loc"

IP_REGEX = re.compile(r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b")


async def resolve_dns_fast(host: str, timeout_sec: float = 0.4) -> Optional[str]:
    """Fast non-blocking DNS resolution with fallback to domain suffix."""
    if not host:
        return None
    cleaned = host.strip()
    if IP_REGEX.match(cleaned):
        return cleaned

    loop = asyncio.get_running_loop()

    def _resolve(h: str) -> Optional[str]:
        try:
            return socket.gethostbyname(h)
        except Exception:
            if "." not in h:
                try:
                    return socket.gethostbyname(f"{h}{DOMAIN_SUFFIX}")
                except Exception:
                    pass
        return None

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _resolve, cleaned), timeout=timeout_sec)
    except Exception:
        return None


async def fast_ping(host: str, timeout_sec: float = PING_TIMEOUT_SEC) -> Dict[str, Any]:
    """Fail-Fast single-packet ICMP ping (timeout 400ms)."""
    is_win = os.name == "nt"
    timeout_ms = max(int(timeout_sec * 1000), 100)
    if is_win:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec + 0.3)
        out_text = stdout.decode("cp866" if is_win else "utf-8", errors="ignore")
        lower_out = out_text.lower()

        has_unreachable = (
            "недоступен" in lower_out
            or "unreachable" in lower_out
            or "100% потерь" in lower_out
            or "100% loss" in lower_out
            or "превышен интервал" in lower_out
            or "timed out" in lower_out
        )
        has_reply = (
            "ttl=" in lower_out
            or "байт=" in lower_out
            or "bytes=" in lower_out
            or "время=" in lower_out
            or "time=" in lower_out
        )

        is_online = (
            (proc.returncode == 0)
            and has_reply
            and not ("100% потерь" in lower_out or "100% loss" in lower_out or has_unreachable)
        )

        rtt_match = re.search(r"(?:время|time)[<=]([0-9\.]+)\s*ms", out_text, re.IGNORECASE)
        if not rtt_match:
            rtt_match = re.search(r"(?:Среднее|Average|avg)[ =]+([0-9\.]+)\s*ms", out_text, re.IGNORECASE)

        avg_rtt = f"{rtt_match.group(1)}ms" if rtt_match else ("<1ms" if is_online else None)

        return {
            "host": host,
            "is_online": is_online,
            "avg_rtt": avg_rtt,
        }
    except (TimeoutError, asyncio.TimeoutError):
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return {"host": host, "is_online": False, "avg_rtt": None, "error": "Ping Timeout (400ms)"}
    except Exception as e:
        if proc:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return {"host": host, "is_online": False, "avg_rtt": None, "error": str(e)}
