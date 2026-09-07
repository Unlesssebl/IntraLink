from typing import Any

from shared.domain import DecisionOutcome, Evidence, ResolutionProposed

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
            comment="Добрый день! Ваша заявка принята в работу. Если у вас возникнут вопросы или дополнения, пожалуйста, напишите в комментариях к этой заявке.",
        )

    def evaluate_typed(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> DecisionOutcome:
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

