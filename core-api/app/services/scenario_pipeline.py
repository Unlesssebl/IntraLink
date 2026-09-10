"""Small orchestration components around the functional decision core."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from shared.domain import ClarificationRequired, FactObservation

import asyncio
from app.services.facts import collect_ticket_observations
from app.services.facts.collectors import (
    collect_deterministic,
    collect_diagnostics,
    collect_structured,
)
from app.services.resolution_service import resolve_outcome
from app.services.scenarios import Scenario, ScenarioContext, ScenarioRegistry
from app.services.scenarios.registry import RouteResult


class ScenarioRouter:
    def __init__(self, registry: ScenarioRegistry):
        self.registry = registry

    def route_result(
        self,
        context: ScenarioContext,
        *,
        pinned_key: str | None = None,
        pinned_version: int | None = None,
    ) -> RouteResult:
        if pinned_key:
            scenario = self.registry.get(pinned_key, pinned_version)
            if scenario is None:
                raise ValueError("pinned_scenario_version_unavailable")
            return RouteResult(
                scenario=scenario,
                score=0.95 if scenario.definition.risk_level >= 2 else 0.85,
                reasons=["pinned_version"],
                is_ambiguous=False,
            )
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


class FactCollectionPlanner:
    def __init__(self, *, include_llm: bool = True):
        self.include_llm = include_llm

    async def collect(
        self,
        task: dict[str, Any],
        *,
        comments: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[FactObservation]:
        if not self.include_llm:
            batches = await asyncio.gather(
                collect_structured(task),
                collect_deterministic(task, comments),
                collect_diagnostics(diagnostics),
            )
            return [observation for batch in batches for observation in batch]
        return await collect_ticket_observations(
            task,
            comments=comments,
            diagnostics=diagnostics,
        )


class PolicyResolver:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve(self, outcome: Any) -> dict[str, Any]:
        return await resolve_outcome(
            self.db,
            outcome.outcome_key,
            getattr(outcome, "context", {}) or {},
            expected_kind=outcome.kind,
            expected_action=getattr(outcome, "action", None),
        )


class ResponseComposer:
    @staticmethod
    def compose(
        *,
        scenario_risk: int,
        outcome: Any,
        policy: dict[str, Any],
        generated_response: str | None,
    ) -> str:
        if (
            scenario_risk >= 2
            or isinstance(outcome, ClarificationRequired)
            or not generated_response
        ):
            return str(policy["comment"])
        return generated_response
