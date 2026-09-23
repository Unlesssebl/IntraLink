"""Scenario Router: combines Catalog Prior, Coherence Guard, and Registry ranking."""

from __future__ import annotations

import logging
from typing import Any

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.catalog_prior import CatalogPriorProvider
from app.services.scenarios.coherence_guard import CoherenceGuard
from app.services.scenarios.contracts import RouteResult
from app.services.scenarios.registry import ScenarioRegistry

logger = logging.getLogger("core_api.scenarios.router")


class ScenarioRouter:
    """
    Единый координатор маршрутизации сценариев:
    1. Проверка pinned_key (если зафиксирован оператором или активным TicketRun).
    2. Определение Catalog Prior (по ServiceId, TaskTypeId, ServiceParentId).
    3. Контроль расхождения Coherence Guard (Negative Barrier).
    4. При кросс-доменной коллизии -> Service Redirect (Статус 30).
    5. При отсутствии Prior -> ранжирование через Registry.
    """

    def __init__(
        self,
        registry: ScenarioRegistry,
        prior_provider: CatalogPriorProvider | None = None,
        coherence_guard: CoherenceGuard | None = None,
    ) -> None:
        self.registry = registry
        self.prior_provider = prior_provider or CatalogPriorProvider()
        self.coherence_guard = coherence_guard or CoherenceGuard()

    def route_result(
        self,
        context: ScenarioContext,
        *,
        pinned_key: str | None = None,
        pinned_version: int | None = None,
    ) -> RouteResult:
        # 1. Pinned scenario override
        if pinned_key:
            scenario = self.registry.get(pinned_key, pinned_version)
            if scenario is None:
                raise ValueError(f"pinned_scenario_version_unavailable:{pinned_key}:{pinned_version}")
            natural_res = self._route_natural(context)
            transition_proposed = None
            if (
                natural_res.scenario.definition.key != pinned_key
                and natural_res.score >= 0.85
                and not natural_res.is_ambiguous
            ):
                transition_proposed = {
                    "current_key": pinned_key,
                    "current_version": pinned_version or scenario.definition.version,
                    "proposed_key": natural_res.scenario.definition.key,
                    "proposed_version": natural_res.scenario.definition.version,
                    "reasons": natural_res.reasons,
                    "score": natural_res.score,
                }
            return RouteResult(
                scenario=scenario,
                score=0.95 if scenario.definition.risk_level >= 2 else 0.85,
                reasons=["pinned_version"],
                is_ambiguous=False,
                transition_proposed=transition_proposed,
            )

        return self._route_natural(context)

    def _route_natural(self, context: ScenarioContext) -> RouteResult:
        # 2. Определение априорной гипотезы по каталогу (Catalog Prior)
        prior = self.prior_provider.get_prior(context.task, context.facts)

        if prior is not None:
            # 3. Проверка смысловой согласованности (Coherence Guard)
            coherence = self.coherence_guard.check_coherence(prior, context)

            if coherence.has_cross_domain_collision:
                # Кросс-доменная коллизия: заявитель ошибся разделом
                redirect_scenario = self.registry.get("redirect")
                if redirect_scenario is not None:
                    return RouteResult(
                        scenario=redirect_scenario,
                        score=0.95,
                        reasons=[f"cross_domain_collision:{coherence.reason}"],
                        is_ambiguous=False,
                        prior=prior,
                        coherence=coherence,
                    )

            if coherence.is_coherent:
                target_scenario = self.registry.get(prior.scenario_key, prior.scenario_version)
                if target_scenario is not None:
                    return RouteResult(
                        scenario=target_scenario,
                        score=prior.confidence,
                        reasons=[f"catalog_prior_coherent:{prior.reason}", coherence.reason],
                        is_ambiguous=False,
                        prior=prior,
                        coherence=coherence,
                    )

        # 4. Unanchored fallback: запуск ранжирования по реестру
        return self.registry.route_result(context)

    def route(
        self,
        context: ScenarioContext,
        *,
        pinned_key: str | None = None,
        pinned_version: int | None = None,
    ) -> Scenario:
        return self.route_result(
            context,
            pinned_key=pinned_key,
            pinned_version=pinned_version,
        ).scenario
