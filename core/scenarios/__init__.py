"""Core Scenario Engine and Declarative Lifecycle Contracts."""

from core.scenarios.base import (
    BaseScenario,
    PreconditionResult,
    ScenarioExecutionResult,
    ScenarioMatch,
)
from core.scenarios.catalog_prior import CatalogPriorProvider
from core.scenarios.coherence_guard import CoherenceGuard, CoherenceResult, CoherenceStatus
from core.scenarios.engine import PlanSynthesizer
from core.scenarios.orchestrator import ScenarioLifecycleOrchestrator
from core.scenarios.registry import (
    ScenarioRegistry,
    get_default_scenario_registry,
    reset_registry,
)
from core.scenarios.reporter import AutopilotReporter, get_autopilot_reporter
from core.scenarios.router import ScenarioRouter
from core.scenarios.semantic_index import SemanticPrototypeIndex

__all__ = [
    "AutopilotReporter",
    "BaseScenario",
    "CatalogPriorProvider",
    "CoherenceGuard",
    "CoherenceResult",
    "CoherenceStatus",
    "PlanSynthesizer",
    "PreconditionResult",
    "ScenarioExecutionResult",
    "ScenarioLifecycleOrchestrator",
    "ScenarioMatch",
    "ScenarioRegistry",
    "ScenarioRouter",
    "SemanticPrototypeIndex",
    "get_autopilot_reporter",
    "get_default_scenario_registry",
    "reset_registry",
]

