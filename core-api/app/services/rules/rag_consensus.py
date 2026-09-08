from typing import Any

from shared.domain import DecisionOutcome, Evidence, NoMatch, ResolutionProposed

from .base import BaseRule, RuleDecision


class RAGConsensusRule(BaseRule):
    """
    Правило: Семантический RAG-консенсус (проверенные исторические решения базы знаний при сходстве >= 90%).
    Применимо ТОЛЬКО для чистых запросов на обслуживание/доступы, но ЗАПРЕЩЕНО для активных неисправностей.
    """

    def __init__(self, priority: int = 15):
        super().__init__(priority=priority)

    @property
    def name(self) -> str:
        return "RAGConsensusRule"

    def evaluate(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> RuleDecision | None:
        if not kb_matches or redirect_mode:
            return None

        name = (task.get("Name") or task.get("name") or "").lower()
        desc = (task.get("Description") or task.get("description") or "").lower()
        user_text = f"{name} {desc}".strip()

        top_kb = kb_matches[0]
        from app.services.rag import is_valid_solution_source
        if not is_valid_solution_source(top_kb):
            return None

        sim = float(top_kb.get("similarity_pct", 0))
        sol = (top_kb.get("solution") or "").strip()
        status_name = top_kb.get("status_name", "")
        res_type = (top_kb.get("resolution_type") or "").lower()

        is_troubleshooting_incident = any(w in user_text for w in [
            "не печатает", "не работает", "ошибка", "сбой", "тормозит", "зависает", "вылетает", "не сканирует", "не включается", "проблема"
        ])

        if sim >= 90.0 and sol and len(sol) >= 15:
            if "выполнен" in status_name.lower() and res_type != "cancelled" and not is_troubleshooting_incident:
                return RuleDecision(
                    template_key="rag_historical_solution",
                    name=f"🧠 Проверенный прецедент базы знаний (#{top_kb.get('task_id')}, сходство {sim}%)",
                    status_id=27,
                    status_name="В работе",
                    expenses=10,
                    comment=(
                        "Заявка принята в работу. Для аналогичной проблемы ранее "
                        f"применялось следующее решение: {sol} "
                        "Проверю применимость этого решения к текущей заявке."
                    ),
                    rag_applied=True,
                    rag_task_id=top_kb.get("task_id"),
                    rag_similarity=sim,
                )

        return None

    def evaluate_typed(
        self,
        task: dict[str, Any],
        diag: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        redirect_mode: bool = False,
        context: dict[str, Any] | None = None,
    ) -> DecisionOutcome:
        dec = self.evaluate(task, diag, kb_matches, redirect_mode, context)
        if dec is not None and dec.rag_applied:
            top_kb = (kb_matches or [{}])[0]
            sol = (top_kb.get("solution") or "").strip()
            return ResolutionProposed(
                rule_key="rag.consensus",
                rule_version="2",
                outcome_key="rag_historical_solution",
                target_status_id=27,
                context={"solution": sol},
                evidence=[
                    Evidence(
                        source="rule",
                        field="rag_consensus",
                        code="historical_match",
                        detail=f"Historical ticket #{dec.rag_task_id} similarity {dec.rag_similarity}%",
                    )
                ],
                metadata={
                    "rag_applied": True,
                    "rag_task_id": dec.rag_task_id,
                    "rag_similarity": dec.rag_similarity,
                },
            )
        return NoMatch(rule_key="rag.consensus", rule_version="2")

