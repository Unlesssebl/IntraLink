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
                CredentialsRule(priority=10),
                PhysicalDeliveryRule(priority=15),
                FileLockRule(priority=20),
                PrinterRule(priority=25),
                RemoteAccessRule(priority=30),
                OfflineHostRule(priority=35),
                ServiceRedirectRule(priority=50),
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
                    logger.debug("Правило '%s' успешно сработало для заявки #%s", rule.name, task.get("Id") or task.get("id"))
                    return decision
            except Exception as e:
                logger.error("Ошибка при выполнении правила '%s': %s", rule.name, e)

        # Fallback по умолчанию, если ни одно правило не вернуло результат
        return StandardInWorkRule().evaluate(task, diag, kb_matches, redirect_mode, context)
