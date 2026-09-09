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
    extract_pc_names_from_text,
    extract_printer_addresses_from_text,
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
from shared.domain import (
    DecisionOutcome,
    PersonCandidate,
    TicketFacts,
    validate_person_candidate,
    SCENARIO_DISPLAY_NAMES,
    SCENARIO_SHORT_NAMES,
    SUPPORTED_SCENARIO_KEYS,
    get_scenario_display_name,
)

__all__ = [
    "normalize_pc_name",
    "normalize_printer_address",
    "is_valid_pc_name",
    "is_valid_printer_name",
    "resolve_pc_candidates",
    "resolve_printer_candidates",
    "extract_pc_names_from_text",
    "extract_printer_addresses_from_text",
    "KNOWN_PC_PREFIXES",
    "KNOWN_PRINTER_PREFIXES",
    "run_host_diagnostics",
    "run_single_host_diag",
    "format_diagnostics_summary",
    "extract_potential_hosts",
    "async_ping",
    "check_tcp_port",
    "resolve_dns",
    "DecisionOutcome",
    "PersonCandidate",
    "TicketFacts",
    "validate_person_candidate",
    "SCENARIO_DISPLAY_NAMES",
    "SCENARIO_SHORT_NAMES",
    "SUPPORTED_SCENARIO_KEYS",
    "get_scenario_display_name",
]

