"""Base definitions and reusable builders for typed scenarios."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from app.services.scenarios.base import Scenario, ScenarioContext
from shared.domain import (
    ActionProposed,
    DecisionOutcome,
    Evidence,
    FactRequirement,
    NoMatch,
    ResolutionProposed,
    ScenarioDefinition,
    ScenarioMatch,
)

Matcher = Callable[[ScenarioContext], tuple[bool, float, str]]


def extract_ticket_text(context: ScenarioContext) -> str:
    """Извлекает нормализованный объединенный текст заявки и внешних комментариев."""
    parts = [
        str(context.task.get("Name") or ""),
        str(context.task.get("Description") or ""),
        str(context.task.get("ServiceName") or ""),
    ]
    author_login = str(
        context.task.get("CreatorLogin") or context.task.get("Creator") or ""
    ).strip().lower()
    for comment in (context.comments or []):
        if not isinstance(comment, dict):
            continue
        author = str(
            comment.get("AuthorLogin")
            or comment.get("CreatorLogin")
            or comment.get("Author")
            or comment.get("Creator")
            or ""
        ).strip().lower()
        is_private = bool(comment.get("IsPrivate") or comment.get("is_private"))
        if is_private:
            continue
        comment_text = str(
            comment.get("Comment")
            or comment.get("comment")
            or comment.get("Text")
            or ""
        ).strip()
        if not comment_text:
            continue
        if comment_text.startswith("---") or "автоматическое оповещение" in comment_text.lower():
            continue
        if not author_login or author == author_login or not author:
            parts.append(comment_text)
    return " ".join(parts).casefold()


def contains_any(*keywords: str) -> Matcher:
    """Простой матчер по вхождению ключевых слов."""
    def matcher(context: ScenarioContext) -> tuple[bool, float, str]:
        text = extract_ticket_text(context)
        found = [keyword for keyword in keywords if keyword in text]
        return bool(found), min(1.0, 0.7 + 0.05 * len(found)), ",".join(found)

    return matcher


def default_standard_in_work_outcome() -> DecisionOutcome:
    """Дефолтный исход: принятие заявки в работу на 1-й линии."""
    return ResolutionProposed(
        rule_key="standard.in_work",
        rule_version="2",
        outcome_key="in_work_standard",
        target_status_id=27,
        evidence=[
            Evidence(
                source="rule",
                field="status_id",
                code="standard_first_line_acceptance",
                detail="Default first-line incident intake",
            )
        ],
    )


class RuleBackedScenario(Scenario):
    """Сценарий, делегирующий принятие решения типизированному вычислителю."""

    def __init__(
        self,
        definition: ScenarioDefinition,
        matcher: Matcher,
        evaluator_factory: Callable[[], Any],
    ) -> None:
        self.definition = definition
        self._matcher = matcher
        self._evaluator_factory = evaluator_factory

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        matched, score, reason = self._matcher(context)
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=matched,
            score=score,
            reason=reason or None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        evaluator_inst = self._evaluator_factory()
        evaluator = getattr(evaluator_inst, "evaluate_typed", evaluator_inst)
        available = {
            "task": context.task,
            "diag": context.diagnostics,
            "kb_matches": context.kb_matches,
            "redirect_mode": context.redirect_mode,
            "context": {"facts": context.facts.model_dump(mode="json")},
        }
        accepted = inspect.signature(evaluator).parameters
        outcome = evaluator(
            **{key: value for key, value in available.items() if key in accepted}
        )
        if isinstance(outcome, NoMatch):
            return default_standard_in_work_outcome()
        return outcome


class FactActionScenario(RuleBackedScenario):
    """Сценарий, формирующий ActionProposed на основе валидированных фактов."""

    def __init__(
        self,
        definition: ScenarioDefinition,
        matcher: Matcher,
        *,
        action: str,
        outcome_key: str,
        parameter_map: dict[str, str],
    ) -> None:
        super().__init__(definition, matcher, default_standard_in_work_outcome)
        self._action = action
        self._outcome_key = outcome_key
        self._parameter_map = parameter_map

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        parameters = {
            target: str(context.facts.valid_value(source, ""))
            for target, source in self._parameter_map.items()
            if context.facts.valid_value(source, "") != ""
        }
        return ActionProposed(
            rule_key=f"scenario.{self.definition.key}",
            rule_version=str(self.definition.version),
            outcome_key=self._outcome_key,
            action=self._action,
            parameters=parameters,
            risk_level=self.definition.risk_level,
            requires_approval=True,
            evidence=[
                Evidence(source="rule", field=source, code="required_fact_valid")
                for source in self._parameter_map.values()
                if context.facts.valid_value(source, "") != ""
            ],
        )
