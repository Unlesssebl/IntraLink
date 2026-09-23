"""Scenario Engine 2.0 package: clean architecture for durable ticket decisioning."""

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.catalog_prior import CatalogPriorProvider
from app.services.scenarios.coherence_guard import CoherenceGuard
from app.services.scenarios.contracts import CoherenceVerdict, PriorHypothesis, RouteResult
from app.services.scenarios.registry import ScenarioRegistry, get_scenario_registry
from app.services.scenarios.router import ScenarioRouter
from app.services.scenarios.shadow_comparator import ShadowComparator, ShadowComparisonResult

__all__ = [
    "Scenario",
    "ScenarioContext",
    "ScenarioRegistry",
    "get_scenario_registry",
    "ScenarioRouter",
    "CatalogPriorProvider",
    "CoherenceGuard",
    "PriorHypothesis",
    "CoherenceVerdict",
    "RouteResult",
    "ShadowComparator",
    "ShadowComparisonResult",
]
