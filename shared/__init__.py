"""
Пакет shared: общие модули нормализации и сетевой экспресс-диагностики
для helpdesk-cli, execution-worker и других компонентов IntraLink.
"""
from shared.normalizer import (
    normalize_pc_name,
    normalize_printer_address,
    is_valid_pc_name,
    is_valid_printer_name,
    resolve_pc_candidates,
    resolve_printer_candidates,
    KNOWN_PC_PREFIXES,
    KNOWN_PRINTER_PREFIXES,
)
from shared.diagnostics import (
    run_host_diagnostics,
    run_single_host_diag,
    format_diagnostics_summary,
    extract_potential_hosts,
    async_ping,
    check_tcp_port,
    resolve_dns,
)

__all__ = [
    "normalize_pc_name",
    "normalize_printer_address",
    "is_valid_pc_name",
    "is_valid_printer_name",
    "resolve_pc_candidates",
    "resolve_printer_candidates",
    "KNOWN_PC_PREFIXES",
    "KNOWN_PRINTER_PREFIXES",
    "run_host_diagnostics",
    "run_single_host_diag",
    "format_diagnostics_summary",
    "extract_potential_hosts",
    "async_ping",
    "check_tcp_port",
    "resolve_dns",
]
