from typing import Any

from .base import BaseRule, RuleDecision


class StandardInWorkRule(BaseRule):
    """
    Правило: Fallback стандартное принятие заявки в работу на 1-й линии (Статус 27).
    """

    def __init__(self, priority: int = 999):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "StandardInWorkRule"

    def evaluate(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> RuleDecision | None:
        return RuleDecision(
            template_key="in_work_standard",
            name="Стандартное принятие в работу",
            status_id=27,
            status_name="В работе",
            expenses=10,
            comment="Добрый день! Ваша заявка принята в работу. По вопросам звоните на номер 49-87.",
        )
