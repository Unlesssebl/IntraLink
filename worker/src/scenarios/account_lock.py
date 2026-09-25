"""Backwards-compatibility re-exports for AccountLockScenario (canonical: core.scenarios.adapters.account_lock)."""

from core.scenarios.adapters.account_lock import (
    ACCOUNT_LOCK_SERVICE_IDS,
    AccountLockScenario,
)

__all__ = [
    "AccountLockScenario",
    "ACCOUNT_LOCK_SERVICE_IDS",
]
