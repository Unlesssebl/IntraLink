"""Scenario: Semantic RAG Consultation from historical knowledge base."""

from __future__ import annotations

import logging
from typing import Any

from app.services.scenarios.base import Scenario, ScenarioContext
from app.services.scenarios.definitions.base_definition import (
    default_standard_in_work_outcome,
    extract_ticket_text,
)
from shared.domain import (
    DecisionOutcome,
    Evidence,
    ResolutionProposed,
    ScenarioDefinition,
    ScenarioMatch,
)

logger = logging.getLogger("core_api.scenarios.rag_consultation")


class RAGConsultationScenario(Scenario):
    """Сценарий консультации на базе проверенных прецедентов RAG (сходство >= 90%)."""

    definition = ScenarioDefinition(
        key="rag_consultation",
        version=1,
        risk_level=0,
    )

    def match(self, context: ScenarioContext) -> ScenarioMatch:
        has_matches = bool(context.kb_matches)
        return ScenarioMatch(
            scenario_key=self.definition.key,
            scenario_version=self.definition.version,
            matched=has_matches,
            score=0.45 if has_matches else 0.0,
            reason="rag_candidates_available" if has_matches else None,
        )

    def decide(self, context: ScenarioContext) -> DecisionOutcome:
        kb_matches = context.kb_matches
        if not kb_matches or context.redirect_mode:
            return default_standard_in_work_outcome()

        task = context.task
        text = extract_ticket_text(context)
        is_troubleshooting = any(w in text for w in [
            "не печатает", "не работает", "ошибка", "сбой", "тормозит",
            "зависает", "вылетает", "не сканирует", "не включается", "проблема"
        ])

        top_kb = kb_matches[0]
        try:
            from app.services.rag import is_valid_solution_source
            if not is_valid_solution_source(top_kb):
                return default_standard_in_work_outcome()
        except Exception:
            pass

        sim = float(top_kb.get("similarity_pct", 0))
        sol = str(top_kb.get("solution") or "").strip()
        status_name = str(top_kb.get("status_name") or "")
        res_type = str(top_kb.get("resolution_type") or "").lower()

        if sim >= 90.0 and sol and len(sol) >= 15:
            if "выполнен" in status_name.lower() and res_type != "cancelled" and not is_troubleshooting:
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
                            detail=f"Historical ticket #{top_kb.get('task_id')} similarity {sim}%",
                        )
                    ],
                    metadata={
                        "rag_applied": True,
                        "rag_task_id": top_kb.get("task_id"),
                        "rag_similarity": sim,
                    },
                )

        return default_standard_in_work_outcome()
