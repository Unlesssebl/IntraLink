"""Ordered, version-aware registry of ticket scenarios."""

from __future__ import annotations

import hashlib

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.builtin import built_in_scenarios

SCENARIO_KEY_ALIASES: dict[str, str] = {
    "user_creation": "create_user",
    "printer_installation": "install_printer",
}
MIN_SCENARIO_SCORE = 0.85
MIN_SCENARIO_MARGIN = 0.10


class ScenarioRegistry:
    def __init__(self, scenarios: tuple[Scenario, ...] | None = None) -> None:
        self._scenarios: dict[tuple[str, int], Scenario] = {}
        self._order: list[tuple[str, int]] = []
        for scenario in scenarios or built_in_scenarios():
            self.register(scenario)

    @staticmethod
    def normalize_key(key: str) -> str:
        return SCENARIO_KEY_ALIASES.get(key, key)

    def register(self, scenario: Scenario) -> None:
        key = (scenario.definition.key, scenario.definition.version)
        if key in self._scenarios:
            raise ValueError(f"duplicate_scenario:{key[0]}:{key[1]}")
        self._scenarios[key] = scenario
        self._order.append(key)

    def get(self, key: str, version: int | None = None) -> Scenario | None:
        canonical_key = self.normalize_key(key)
        if version is not None:
            return self._scenarios.get((canonical_key, version)) or self._scenarios.get((key, version))
        versions = [item for item in self._order if item[0] in (canonical_key, key)]
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
        ranked = sorted(
            eligible,
            key=lambda item: (item[0].score, -item[1]),
            reverse=True,
        )
        domain = [item for item in ranked if item[0].score >= MIN_SCENARIO_SCORE]
        if domain:
            top = domain[0]
            runner_up = domain[1] if len(domain) > 1 else None
            if runner_up is None or top[0].score - runner_up[0].score >= MIN_SCENARIO_MARGIN:
                return top[2]
        fallback = next(
            (
                item[2]
                for item in ranked
                if item[0].scenario_key == "consultation"
            ),
            None,
        )
        if fallback is None:
            raise ValueError("ambiguous_scenario_without_fallback")
        return fallback

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
