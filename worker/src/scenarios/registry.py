"""Backwards-compatibility re-exports for ScenarioRegistry (canonical: core.scenarios.registry)."""

from core.scenarios.registry import (
    ScenarioRegistry,
    get_default_scenario_registry,
    reset_registry,
)

__all__ = [
    "ScenarioRegistry",
    "get_default_scenario_registry",
    "reset_registry",
]
