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


from dataclasses import dataclass, field

@dataclass(slots=True)
class RouteResult:
    scenario: Scenario
    score: float
    runner_up_score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    is_ambiguous: bool = False
    transition_proposed: dict[str, Any] | None = None


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

    def route_result(self, context: ScenarioContext) -> RouteResult:
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
        domain = [
            item for item in ranked
            if item[0].scenario_key not in ("rag_consultation", "consultation")
            and item[0].score >= MIN_SCENARIO_SCORE
        ]
        if domain:
            top = domain[0]
            runner_up = domain[1] if len(domain) > 1 else None
            top_match = top[0]
            top_scenario = top[2]
            runner_up_score = runner_up[0].score if runner_up is not None else 0.0
            reasons = [top_match.reason] if top_match.reason else []

            if runner_up is not None and (top_match.score - runner_up_score) < MIN_SCENARIO_MARGIN:
                return RouteResult(
                    scenario=top_scenario,
                    score=top_match.score,
                    runner_up_score=runner_up_score,
                    reasons=reasons + [f"ambiguous_with:{runner_up[0].scenario_key}"],
                    is_ambiguous=True,
                )
            return RouteResult(
                scenario=top_scenario,
                score=top_match.score,
                runner_up_score=runner_up_score,
                reasons=reasons,
                is_ambiguous=False,
            )

        rag_match = next(
            (item for item in ranked if item[0].scenario_key == "rag_consultation"),
            None,
        )
        if rag_match is not None and bool(context.kb_matches):
            return RouteResult(
                scenario=rag_match[2],
                score=rag_match[0].score,
                reasons=[rag_match[0].reason] if rag_match[0].reason else [],
                is_ambiguous=False,
            )

        fallback = next(
            (item for item in ranked if item[0].scenario_key == "consultation"),
            None,
        )
        if fallback is None:
            raise ValueError("ambiguous_scenario_without_fallback")
        return RouteResult(
            scenario=fallback[2],
            score=fallback[0].score,
            reasons=[fallback[0].reason] if fallback[0].reason else [],
            is_ambiguous=False,
        )

    def route(self, context: ScenarioContext) -> Scenario:
        res = self.route_result(context)
        if res.is_ambiguous:
            fallback = next(
                (scenario for key, scenario in self._scenarios.items() if key[0] == "consultation"),
                None,
            )
            return fallback or res.scenario
        return res.scenario

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
