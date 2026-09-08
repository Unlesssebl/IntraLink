"""Built-in rule-backed scenario definitions."""

from __future__ import annotations

from typing import Any, Callable
import inspect

from shared.domain import (
    ActionProposed,
    DecisionOutcome,
    Evidence,
    FactRequirement,
    NoMatch,
    ScenarioDefinition,
    ScenarioMatch,
)

from app.services.rules.credentials import CredentialsRule
from app.services.rules.file_locks import FileLockRule
from app.services.rules.offline_host import OfflineHostRule
from app.services.rules.physical_device import PhysicalDeliveryRule
from app.services.rules.rag_consensus import RAGConsensusRule
from app.services.rules.redirect import ServiceRedirectRule
from app.services.rules.standard import StandardInWorkRule
from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.template_engine import detect_service_redirect


Matcher = Callable[[ScenarioContext], tuple[bool, float, str]]


def _text(context: ScenarioContext) -> str:
    return f"{context.task.get('Name') or ''} {context.task.get('Description') or ''}".casefold()


def _contains(*keywords: str) -> Matcher:
    def matcher(context: ScenarioContext) -> tuple[bool, float, str]:
        text = _text(context)
        found = [keyword for keyword in keywords if keyword in text]
        return bool(found), min(1.0, 0.7 + 0.05 * len(found)), ",".join(found)

    return matcher


def _create_user_match(context: ScenarioContext) -> tuple[bool, float, str]:
    text = _text(context)
    phrases = (
        "создать учет",
        "создать учёт",
        "создание учет",
        "создание учёт",
        "завести учет",
        "завести учёт",
        "создать пользователя",
        "новый сотрудник",
    )
    found = [phrase for phrase in phrases if phrase in text]
    return bool(found), 0.92 if found else 0.0, ",".join(found)


def _redirect_match(context: ScenarioContext) -> tuple[bool, float, str]:
    redirect = detect_service_redirect(context.task)
    if redirect:
        return True, 1.0, str(redirect.get("reason") or "catalog_redirect")
    return False, 0.0, ""


class RuleBackedScenario(Scenario):
    def __init__(
        self,
        definition: ScenarioDefinition,
        matcher: Matcher,
        rule_factory: Callable[[], Any],
    ) -> None:
        self.definition = definition
        self._matcher = matcher
        self._rule_factory = rule_factory

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
        evaluator = self._rule_factory().evaluate_typed
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
            return StandardInWorkRule().evaluate_typed(
                context.task,
                context.diagnostics,
                context.kb_matches,
                context.redirect_mode,
                {"facts": context.facts.model_dump(mode="json")},
            )
        return outcome


class ConsultationScenario(RuleBackedScenario):
    def match(self, context: ScenarioContext) -> ScenarioMatch:
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=True,
            score=0.01,
            reason="deterministic_fallback",
        )


class FactActionScenario(RuleBackedScenario):
    def __init__(
        self,
        definition: ScenarioDefinition,
        matcher: Matcher,
        *,
        action: str,
        outcome_key: str,
        parameter_map: dict[str, str],
    ) -> None:
        super().__init__(definition, matcher, StandardInWorkRule)
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


def built_in_scenarios() -> tuple[Scenario, ...]:
    return (
        RuleBackedScenario(
            ScenarioDefinition(
                key="redirect",
                version=1,
                risk_level=1,
                allowed_actions=["apply_triage"],
            ),
            _redirect_match,
            ServiceRedirectRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="create_user",
                version=1,
                risk_level=2,
                allowed_actions=["create_user"],
                clarification_outcome_key="account_details_invalid",
                success_outcome_key="user_created",
                required_facts=[
                    FactRequirement(key="surname", clarification_key="clarify_surname"),
                    FactRequirement(key="name", clarification_key="clarify_name"),
                    FactRequirement(key="company", clarification_key="clarify_company"),
                    FactRequirement(key="department", clarification_key="clarify_department"),
                    FactRequirement(key="title", clarification_key="clarify_title"),
                ],
            ),
            _create_user_match,
            CredentialsRule,
        ),
        FactActionScenario(
            ScenarioDefinition(
                key="grant_wlan",
                version=1,
                risk_level=2,
                allowed_actions=["grant_wlan"],
                clarification_outcome_key="account_details_invalid",
                success_outcome_key="resolved_standard",
                required_facts=[
                    FactRequirement(key="identity", clarification_key="clarify_identity")
                ],
            ),
            _contains("wlan", "wi-fi", "wifi", "вайф"),
            action="grant_wlan",
            outcome_key="grant_wlan_proposed",
            parameter_map={"identity": "identity"},
        ),
        FactActionScenario(
            ScenarioDefinition(
                key="install_printer",
                version=1,
                risk_level=1,
                allowed_actions=["install_printer"],
                clarification_outcome_key="printer_ip_clarify",
                success_outcome_key="resolved_standard",
                required_facts=[
                    FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
                    FactRequirement(
                        key="printer_name", clarification_key="clarify_printer_name"
                    ),
                ],
            ),
            _contains("принтер", "мфу", "печать", "сканер"),
            action="install_printer",
            outcome_key="install_printer_proposed",
            parameter_map={
                "pc_name": "pc_name",
                "printer_name": "printer_name",
                "printer_ip": "printer_address",
            },
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="offline_host",
                version=1,
                risk_level=0,
                clarification_outcome_key="pc_offline",
                required_facts=[
                    FactRequirement(key="pc_name", clarification_key="clarify_pc_name")
                ],
            ),
            _contains("не доступ", "недоступ", "offline", "не в сети"),
            OfflineHostRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(key="file_lock", version=1, risk_level=1),
            _contains("файл занят", "файл заблокирован", "открыт другим", "сетевой файл"),
            FileLockRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(key="physical_device", version=1, risk_level=1),
            _contains("системный блок", "ноутбук", "монитор", "ремонт оборудования"),
            PhysicalDeliveryRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(key="rag_consultation", version=1, risk_level=0),
            lambda context: (bool(context.kb_matches), 0.45, "rag_candidates_available"),
            RAGConsensusRule,
        ),
        ConsultationScenario(
            ScenarioDefinition(key="consultation", version=1, risk_level=0),
            lambda context: (True, 0.01, "fallback"),
            StandardInWorkRule,
        ),
    )
