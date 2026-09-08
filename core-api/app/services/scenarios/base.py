"""Base interfaces for typed ticket scenarios."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from shared.domain import (
    ActionProposed,
    DecisionOutcome,
    FactBag,
    FactRequirement,
    ScenarioDefinition,
    ScenarioMatch,
)


@dataclass(slots=True)
class ScenarioContext:
    task: dict[str, Any]
    facts: FactBag
    diagnostics: dict[str, Any] | None = None
    kb_matches: list[dict[str, Any]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    redirect_mode: bool = False


class Scenario(ABC):
    definition: ScenarioDefinition

    @abstractmethod
    def match(self, context: ScenarioContext) -> ScenarioMatch:
        raise NotImplementedError

    def requirements(self, context: ScenarioContext) -> tuple[FactRequirement, ...]:
        return tuple(self.definition.required_facts)

    @abstractmethod
    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        raise NotImplementedError

    def build_action(
        self, outcome: DecisionOutcome
    ) -> ActionProposed | None:
        if isinstance(outcome, ActionProposed):
            if outcome.action not in self.definition.allowed_actions:
                raise ValueError(
                    f"scenario_action_not_allowed:{self.definition.key}:{outcome.action}"
                )
            return outcome
        return None
