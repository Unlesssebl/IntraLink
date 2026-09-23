"""Domain contracts for Scenario Engine 2.0."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.services.scenarios.base import Scenario


@dataclass(frozen=True, slots=True)
class PriorHypothesis:
    """Априорная гипотеза целевого сценария на основе метаданных каталога."""
    scenario_key: str
    scenario_version: int = 1
    confidence: float = 0.95
    source: Literal["service_id", "service_parent", "task_type", "database"] = "service_id"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CoherenceVerdict:
    """Результат проверки смысловой согласованности с априорным сценарием."""
    is_coherent: bool = True
    has_cross_domain_collision: bool = False
    divergence_penalty: float = 0.0
    collision_target_root: str | None = None
    reason: str = ""


@dataclass(slots=True)
class RouteResult:
    """Итоговый результат маршрутизации сценария с полной трассировкой."""
    scenario: Scenario
    score: float
    runner_up_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    is_ambiguous: bool = False
    prior: PriorHypothesis | None = None
    coherence: CoherenceVerdict | None = None
    transition_proposed: dict[str, Any] | None = None
