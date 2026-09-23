"""Low-level diagnostic probes: Ping, SMB:445, WinRM:5985."""

from core.diagnostic.ping import fast_ping, resolve_dns_fast
from core.diagnostic.ports import probe_diagnostic_ports, probe_tcp_port

__all__ = [
    "fast_ping",
    "resolve_dns_fast",
    "probe_tcp_port",
    "probe_diagnostic_ports",
]
