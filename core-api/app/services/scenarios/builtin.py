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


def _printer_install_match(context: ScenarioContext) -> tuple[bool, float, str]:
    dev_type = context.facts.valid_value("device_type")
    if dev_type in ("audio", "other"):
        return False, 0.0, "non_printer_device"

    text = _text(context)
    if any(tok in text for tok in ("наушник", "колонки", "коллонки", "гарнитур", "микрофон")) and not any(
        tok in text for tok in ("принтер", "мфу", "printer")
    ):
        return False, 0.0, "audio_device"

    device_tokens = ("принтер", "мфу", "printer")
    install_tokens = (
        "установ",
        "подключ",
        "добав",
        "настроить новый",
        "настройка нового",
        "переустанов",
    )
    failure_tokens = (
        "не печатает",
        "не сканирует",
        "замят",
        "полос",
        "ошибка печати",
        "очередь зависла",
        "не подключа",
        "не видит",
    )
    has_device = any(token in text for token in device_tokens) or bool(
        context.facts.valid_value("printer_name")
    )
    matched_intents = [token for token in install_tokens if token in text]
    matched_failures = [token for token in failure_tokens if token in text]
    is_reinstall = "переустанов" in text
    matched = has_device and bool(matched_intents) and (not matched_failures or is_reinstall)
    reason = ",".join([*matched_intents, *matched_failures])
    return matched, 0.94 if matched else 0.0, reason


def _peripheral_setup_match(context: ScenarioContext) -> tuple[bool, float, str]:
    text = _text(context)
    dev_type = context.facts.valid_value("device_type")
    audio_keywords = ("колонк", "коллонк", "наушник", "микрофон", "гарнитур", "звук", "динамик")
    is_peripheral = dev_type in ("audio", "other") or any(kw in text for kw in audio_keywords)
    if not is_peripheral:
        return False, 0.0, ""

    install_keywords = ("установ", "подключ", "настро", "добав")
    failure_keywords = (
        "не подключа", "не работ", "не видит", "не слыш", "нет звука",
        "хрип", "фонит", "тихо", "отходит", "сбоит", "не определя",
        "проблема с", "не находит", "не может найти"
    )

    has_install = any(kw in text for kw in install_keywords)
    has_failure = any(kw in text for kw in failure_keywords)

    if has_install and not has_failure:
        matched = [kw for kw in install_keywords if kw in text]
        return True, 0.95, ",".join(matched)
    return False, 0.0, ""


def _peripheral_diag_match(context: ScenarioContext) -> tuple[bool, float, str]:
    text = _text(context)
    dev_type = context.facts.valid_value("device_type")
    audio_keywords = ("колонк", "коллонк", "наушник", "микрофон", "гарнитур", "звук", "динамик")
    is_peripheral = dev_type in ("audio", "other") or any(kw in text for kw in audio_keywords)
    if not is_peripheral:
        return False, 0.0, ""

    failure_keywords = (
        "не подключа", "не работ", "не видит", "не слыш", "нет звука",
        "хрип", "фонит", "тихо", "отходит", "сбоит", "не определя",
        "проблема с", "не находит", "не может найти"
    )
    has_failure = any(kw in text for kw in failure_keywords)
    if has_failure:
        matched = [kw for kw in failure_keywords if kw in text]
        return True, 0.95, ",".join(matched)
    return False, 0.0, ""


def _pc_performance_match(context: ScenarioContext) -> tuple[bool, float, str]:
    text = _text(context)
    phrases = (
        "медленно грузит",
        "долго грузится",
        "очень долго",
        "все грузится",
        "тормозит компьютер",
        "медленно работает",
        "зависает компьютер",
        "зависает пк",
        "зависает весь пк",
        "виснет пк",
        "виснет компьютер",
        "виснет комп",
        "сильно виснет",
        "невозможно работать,тормозит",
        "программы могут загружаться",
        "компьютер медленно",
    )
    found = [p for p in phrases if p in text]
    if found:
        return True, 0.95, ",".join(found)
    return False, 0.0, ""


def _network_diag_match(context: ScenarioContext) -> tuple[bool, float, str]:
    text = _text(context)
    phrases = (
        "прерывается интернет",
        "прерывается работа интернета",
        "отваливается сеть",
        "обрыв сети",
        "потери пакетов",
        "потеря пакетов",
        "не работает интернет",
        "пропадает интернет",
        "нет доступа к сети",
        "падает сеть",
        "сетевой сбой",
    )
    found = [p for p in phrases if p in text]
    if not found:
        return False, 0.0, ""

    # Если в тикете явно заявлены общие тормоза ПК, проблемы сети являются сопутствующими
    pc_lag_phrases = ("медленно грузит", "долго грузится", "все грузится", "тормозит компьютер", "зависает пк", "виснет комп")
    is_secondary = any(p in text for p in pc_lag_phrases)
    score = 0.80 if is_secondary else 0.95
    return True, score, ",".join(found)


def _os_reinstall_match(context: ScenarioContext) -> tuple[bool, float, str]:
    text = _text(context)
    phrases = (
        "переустановка операционной системы",
        "переустановка ос",
        "переустановить ос",
        "переустановка windows",
        "переустановить windows",
        "переустановить виндовс",
        "переустановка винды",
        "переустановить винду",
        "надо переустановить ос",
    )
    found = [p for p in phrases if p in text]
    if found:
        return True, 0.95, ",".join(found)
    return False, 0.0, ""


def _printer_failure_match(kind: str) -> Matcher:
    def matcher(context: ScenarioContext) -> tuple[bool, float, str]:
        text = _text(context)
        has_device = any(token in text for token in ("принтер", "мфу", "плоттер", "сканер"))
        business_document = any(
            token in text
            for token in ("ттн", "доверенност", "подпис", "оплат", "в программе тис")
        )
        if not has_device or business_document:
            return False, 0.0, ""
        patterns = {
            "printer_hardware_service": ("вызовите сервис", "сбой аппарата", "c7990", "с7990", "замят"),
            "printer_scan_failure": ("не сканирует", "скан не", "ошибка скан", "сканер"),
            "printer_print_failure": ("не печатает", "нет принтера", "очередь", "без остановки", "не принимает задания"),
        }
        found = [token for token in patterns[kind] if token in text]
        return bool(found), 0.96 if found else 0.0, ",".join(found)

    return matcher


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


class PrinterInstallScenario(FactActionScenario):
    def requirements(
        self, context: ScenarioContext
    ) -> tuple[FactRequirement, ...]:
        requirements = [
            FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
            FactRequirement(key="printer_name", clarification_key="clarify_printer_name"),
        ]
        if context.facts.valid_value("printer_connection_type") not in {"usb"}:
            requirements.append(
                FactRequirement(
                    key="printer_address", clarification_key="clarify_printer_address"
                )
            )
        return tuple(requirements)

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        outcome = super().decide(context)
        targets = context.facts.valid_value("printer_targets", [])
        if isinstance(outcome, ActionProposed) and targets:
            outcome.parameters["printer_targets"] = targets
        return outcome


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
                key="printer_hardware_service",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            _printer_failure_match("printer_hardware_service"),
            StandardInWorkRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="printer_scan_failure",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            _printer_failure_match("printer_scan_failure"),
            StandardInWorkRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="printer_print_failure",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            _printer_failure_match("printer_print_failure"),
            StandardInWorkRule,
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
            # Version 1 remains addressable for already pinned runs, but new
            # routing is handled exclusively by the stricter version 2 matcher.
            lambda _context: (False, 0.0, "legacy_pinned_only"),
            action="install_printer",
            outcome_key="install_printer_proposed",
            parameter_map={
                "pc_name": "pc_name",
                "printer_name": "printer_name",
                "printer_ip": "printer_address",
            },
        ),
        PrinterInstallScenario(
            ScenarioDefinition(
                key="install_printer",
                version=2,
                risk_level=1,
                allowed_actions=["install_printer"],
                clarification_outcome_key="printer_ip_clarify",
                success_outcome_key="resolved_standard",
                required_facts=[
                    FactRequirement(key="pc_name", clarification_key="clarify_pc_name"),
                    FactRequirement(
                        key="printer_name", clarification_key="clarify_printer_name"
                    ),
                    FactRequirement(
                        key="printer_address", clarification_key="clarify_printer_address"
                    ),
                ],
            ),
            _printer_install_match,
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
                key="peripheral_setup",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
                clarification_outcome_key="peripheral_clarify",
                required_facts=[
                    FactRequirement(key="pc_name", clarification_key="clarify_pc_name")
                ],
            ),
            _peripheral_setup_match,
            StandardInWorkRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="peripheral_diagnostics",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
                clarification_outcome_key="peripheral_clarify",
                required_facts=[
                    FactRequirement(key="pc_name", clarification_key="clarify_pc_name")
                ],
            ),
            _peripheral_diag_match,
            StandardInWorkRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="pc_performance",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            _pc_performance_match,
            StandardInWorkRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="network_diagnostics",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            _network_diag_match,
            StandardInWorkRule,
        ),
        RuleBackedScenario(
            ScenarioDefinition(
                key="os_reinstallation",
                version=1,
                risk_level=0,
                allowed_actions=["apply_triage"],
            ),
            _os_reinstall_match,
            StandardInWorkRule,
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
