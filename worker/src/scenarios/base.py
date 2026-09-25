"""Backwards-compatibility re-exports for BaseScenario (canonical location: core.scenarios.base)."""

from core.scenarios.base import (
    BaseScenario,
    PreconditionResult,
    ScenarioExecutionResult,
    ScenarioMatch,
)

__all__ = [
    "BaseScenario",
    "PreconditionResult",
    "ScenarioExecutionResult",
    "ScenarioMatch",
]
