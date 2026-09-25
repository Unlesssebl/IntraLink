"""Worker scenario facades (canonical location: core.scenarios)."""

from core.scenarios import (
    BaseScenario,
    CatalogPriorProvider,
    CoherenceGuard,
    CoherenceResult,
    CoherenceStatus,
    PreconditionResult,
    ScenarioExecutionResult,
    ScenarioMatch,
    ScenarioRegistry,
    ScenarioRouter,
    SemanticPrototypeIndex,
    get_default_scenario_registry,
    reset_registry,
)

__all__ = [
    "BaseScenario",
    "CatalogPriorProvider",
    "CoherenceGuard",
    "CoherenceResult",
    "CoherenceStatus",
    "PreconditionResult",
    "ScenarioExecutionResult",
    "ScenarioMatch",
    "ScenarioRegistry",
    "ScenarioRouter",
    "SemanticPrototypeIndex",
    "get_default_scenario_registry",
    "reset_registry",
]
