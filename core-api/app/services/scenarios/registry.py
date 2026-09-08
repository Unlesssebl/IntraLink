"""Ordered, version-aware registry of ticket scenarios."""

from __future__ import annotations

import hashlib

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.builtin import built_in_scenarios


class ScenarioRegistry:
    def __init__(self, scenarios: tuple[Scenario, ...] | None = None) -> None:
        self._scenarios: dict[tuple[str, int], Scenario] = {}
        self._order: list[tuple[str, int]] = []
        for scenario in scenarios or built_in_scenarios():
            self.register(scenario)

    def register(self, scenario: Scenario) -> None:
        key = (scenario.definition.key, scenario.definition.version)
        if key in self._scenarios:
            raise ValueError(f"duplicate_scenario:{key[0]}:{key[1]}")
        self._scenarios[key] = scenario
        self._order.append(key)

    def get(self, key: str, version: int | None = None) -> Scenario | None:
        if version is not None:
            return self._scenarios.get((key, version))
        versions = [item for item in self._order if item[0] == key]
        if not versions:
            return None
        return self._scenarios[max(versions, key=lambda item: item[1])]

    def route(self, context: ScenarioContext) -> Scenario:
        matches = [
            (scenario.match(context), position, scenario)
            for position, key in enumerate(self._order)
            for scenario in [self._scenarios[key]]
        ]
        eligible = [item for item in matches if item[0].matched]
        if not eligible:
            raise ValueError("no_scenario_fallback_registered")
        return max(eligible, key=lambda item: (item[0].score, -item[1]))[2]

    def all(self) -> tuple[Scenario, ...]:
        return tuple(self._scenarios[key] for key in self._order)

    @staticmethod
    def canary_selected(task_id: int, percent: int) -> bool:
        if percent <= 0:
            return False
        if percent >= 100:
            return True
        bucket = int(hashlib.sha256(str(task_id).encode()).hexdigest()[:8], 16) % 100
        return bucket < percent


_REGISTRY: ScenarioRegistry | None = None


def get_scenario_registry() -> ScenarioRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ScenarioRegistry()
    return _REGISTRY
