"""Small orchestration components around the functional decision core."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from shared.domain import ClarificationRequired, FactObservation

from app.services.facts import collect_ticket_observations
from app.services.resolution_service import resolve_outcome
from app.services.scenarios import Scenario, ScenarioContext, ScenarioRegistry


class ScenarioRouter:
    def __init__(self, registry: ScenarioRegistry):
        self.registry = registry

    def route(
        self,
        context: ScenarioContext,
        *,
        pinned_key: str | None = None,
        pinned_version: int | None = None,
    ) -> Scenario:
        scenario = (
            self.registry.get(pinned_key, pinned_version)
            if pinned_key
            else self.registry.route(context)
        )
        if scenario is None:
            raise ValueError("pinned_scenario_version_unavailable")
        return scenario


class FactCollectionPlanner:
    async def collect(
        self,
        task: dict[str, Any],
        *,
        comments: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[FactObservation]:
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
