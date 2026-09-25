"""Backwards-compatibility re-exports for OfflineHostScenario (canonical: core.scenarios.adapters.offline_host)."""

from core.scenarios.adapters.offline_host import (
    OFFLINE_KEYWORDS,
    OfflineHostScenario,
    check_tcp_port,
)

__all__ = [
    "OFFLINE_KEYWORDS",
    "OfflineHostScenario",
    "check_tcp_port",
]
