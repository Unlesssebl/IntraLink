import logging
from typing import Any

from .base import BaseRule, RuleDecision
from .credentials import CredentialsRule
from .file_locks import FileLockRule
from .offline_host import OfflineHostRule
from .physical_device import PhysicalDeliveryRule
from .printers import PrinterRule
from .rag_consensus import RAGConsensusRule
from .redirect import ServiceRedirectRule
from .remote_access import RemoteAccessRule
from .standard import StandardInWorkRule

logger = logging.getLogger("core_api.rules")


class RuleEngine:
    """
    Пайплайн исполнения изолированных правил триажа и шаблонизации Helpdesk.
    Запускает правила в порядке их приоритета (от наименьшего числа к наибольшему).
    """

    def __init__(self, rules: list[BaseRule] | None = None):
        if rules is None:
            self._rules = [
                # Корректность каталога проверяется до тематических правил:
                # заявка в неверном разделе не должна получить исполняемое
                # решение (например, grant_wlan) вместо редиректа.
                ServiceRedirectRule(priority=5),
                CredentialsRule(priority=10),
                # Сначала даем принтерному правилу сформировать специфичный
                # ответ для недоступного МФУ, затем применяем общий offline gate.
                PrinterRule(priority=12),
                OfflineHostRule(priority=13),
                PhysicalDeliveryRule(priority=15),
                FileLockRule(priority=20),
                RemoteAccessRule(priority=30),
                RAGConsensusRule(priority=60),
                StandardInWorkRule(priority=999),
            ]
        else:
            self._rules = rules
        self._sort_rules()

    def _sort_rules(self) -> None:
        self._rules.sort(key=lambda r: r.priority)

    def add_rule(self, rule: BaseRule) -> None:
        """Добавляет новое правило в пайплайн."""
        self._rules.append(rule)
        self._sort_rules()

    def evaluate(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> RuleDecision:
        """
        Прогоняет контекст заявки через цепочку правил до первого совпадения.
        """
        decision, _trace = self.evaluate_with_trace(
            task=task,
            diag=diag,
            kb_matches=kb_matches,
            redirect_mode=redirect_mode,
            context=context,
        )
        return decision

    def evaluate_with_trace(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> tuple[RuleDecision, list[dict[str, Any]]]:
        """Evaluate rules and return an audit-safe trace without model reasoning."""
        def _apply_downtime_safety(dec: RuleDecision) -> RuleDecision:
            name = task.get("Name") or ""
            desc = task.get("Description") or ""
            full_text = f"{name}. {desc}".lower()
            from .redirect import DOWNTIME_KEYWORDS
            found = [kw for kw in DOWNTIME_KEYWORDS if kw in full_text]
            if found:
                dec.risk_level = "critical"
                dec.risk_warning = (
                    f"Внимание: обнаружен риск производственного простоя ({', '.join(found)})! "
                    "Автоматическая отмена запрещена регламентом безопасности."
                )
                if not dec.trigger_markers:
                    dec.trigger_markers = found
                else:
                    for m in found:
                        if m not in dec.trigger_markers:
                            dec.trigger_markers.append(m)
                if dec.status_id == 30:
                    dec.status_id = 27
                    dec.status_name = "В работе"
                    dec.name = f"Приоритетная обработка ({', '.join(found[:2])})"
            return dec

        trace: list[dict[str, Any]] = []
        for rule in self._rules:
            try:
                decision = rule.evaluate(
                    task=task,
                    diag=diag,
                    kb_matches=kb_matches,
                    redirect_mode=redirect_mode,
                    context=context,
                )
                if decision is not None:
                    decision = _apply_downtime_safety(decision)
                    trace.append(
                        {
                            "rule": rule.name,
                            "priority": rule.priority,
                            "status": "matched",
                            "template_key": decision.template_key,
                        }
                    )
                    logger.debug("Правило '%s' успешно сработало для заявки #%s", rule.name, task.get("Id") or task.get("id"))
                    return decision, trace
                trace.append(
                    {"rule": rule.name, "priority": rule.priority, "status": "not_matched"}
                )
            except Exception as e:
                trace.append(
                    {
                        "rule": rule.name,
                        "priority": rule.priority,
                        "status": "error",
                        "error_type": type(e).__name__,
                    }
                )
                logger.error("Ошибка при выполнении правила '%s': %s", rule.name, e)

        # Fallback по умолчанию, если ни одно правило не вернуло результат
        fallback = StandardInWorkRule().evaluate(task, diag, kb_matches, redirect_mode, context)
        fallback = _apply_downtime_safety(fallback)
        trace.append(
            {
                "rule": "StandardInWorkRule",
                "priority": 999,
                "status": "fallback",
                "template_key": fallback.template_key,
            }
        )
        return fallback, trace
