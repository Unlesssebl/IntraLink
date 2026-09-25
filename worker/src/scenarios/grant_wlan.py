"""Backwards-compatibility re-exports for GrantWLANScenario (canonical: core.scenarios.adapters.grant_wlan)."""

from core.scenarios.adapters.grant_wlan import (
    WLAN_SERVICE_IDS,
    GrantWLANScenario,
)

__all__ = [
    "GrantWLANScenario",
    "WLAN_SERVICE_IDS",
]
