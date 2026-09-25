"""Backwards-compatibility re-exports for AccountCreateScenario (canonical: core.scenarios.adapters.account_create)."""

from core.scenarios.adapters.account_create import (
    ACCOUNT_CREATE_SERVICE_IDS,
    AccountCreateScenario,
)

__all__ = [
    "AccountCreateScenario",
    "ACCOUNT_CREATE_SERVICE_IDS",
]
