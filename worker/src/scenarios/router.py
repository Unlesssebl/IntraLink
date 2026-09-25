"""Backwards-compatibility re-exports for ScenarioRouter (canonical: core.scenarios.router)."""

from core.scenarios.router import (
    WEIGHT_A_DIRECT,
    WEIGHT_B_CATALOG,
    WEIGHT_C_ENTITIES,
    WEIGHT_E_SEMANTIC,
    ScenarioRouter,
)

__all__ = [
    "ScenarioRouter",
    "WEIGHT_A_DIRECT",
    "WEIGHT_B_CATALOG",
    "WEIGHT_C_ENTITIES",
    "WEIGHT_E_SEMANTIC",
]
