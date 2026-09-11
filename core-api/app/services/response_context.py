"""Response context builder and Capacity-Aware prompt variant generation."""

from __future__ import annotations

import json
from typing import Any, Literal
from pydantic import Field

from app.config import settings
from app.services.ai.schemas import PromptVariant
from shared.domain.models import StrictModel


class RagReference(StrictModel):
    task_id: int
    score: float = 0.0
    profile: Literal["local", "cloud"] = "local"
    problem: str
    solution: str
    service_id: int | None = None
    similarity_pct: float = 0.0


class ResponseContext(StrictModel):
    decision_id: str
    decision_version: int
    scenario_key: str
    scenario_version: int
    outcome_kind: str
    plan_phase: str | None = None
    policy_text: str
    allowed_evidence_refs: list[str] = Field(default_factory=list)
    confirmed_facts: dict[str, Any] = Field(default_factory=dict)
    public_plan_steps: list[dict[str, str]] = Field(default_factory=list)
    rag_candidates: list[RagReference] = Field(default_factory=list)


class ResponseContextBuilder:
    """
    Строит изолированный контекст для презентационного слоя (AI / Template).
    Исключает попадание секретов, raw-логов, internal_summary и недопустимых действий.
    """

    @staticmethod
    def build_context(
        *,
        decision_id: str,
        decision_version: int,
        scenario_key: str,
        scenario_version: int,
        outcome_kind: str,
        policy_comment: str,
        facts_summary: dict[str, Any],
        evidence_refs: list[str],
        plan: Any = None,
        kb_matches: list[dict[str, Any]] | None = None,
    ) -> ResponseContext:
        safe_facts: dict[str, Any] = {}
        for key, value in facts_summary.items():
            if (
                isinstance(value, dict)
                and value.get("state") == "valid"
                and value.get("value") not in (None, "", "<redacted>")
            ):
                safe_facts[key] = value.get("value")

        public_steps: list[dict[str, str]] = []
        plan_phase = None
        if plan is not None:
            plan_phase = getattr(plan, "phase", None)
            if hasattr(plan, "steps") and plan.steps:
                for step in plan.steps:
                    title = getattr(step, "title", None) or ""
                    status = getattr(step, "status", None)
                    status_str = status.value if hasattr(status, "value") else str(status or "")
                    executor = getattr(step, "executor", None)
                    if executor in ("windows", "engineer"):
                        # Инженерные шаги не передаются как инструкция заявителю
                        continue
                    if title:
                        public_steps.append({"title": title, "status": status_str})

        rag_refs: list[RagReference] = []
        if kb_matches:
            for item in kb_matches:
                problem = str(item.get("problem") or item.get("name") or "").strip()
                solution = str(item.get("solution") or "").strip()
                if not solution or solution.lower() in ("нет решения", "null", "none"):
                    continue
                tid = int(item.get("task_id") or item.get("id") or 0)
                sim = float(item.get("similarity_pct") or 0.0)
                rag_refs.append(
                    RagReference(
                        task_id=tid,
                        score=round(sim / 100.0, 3) if sim > 1.0 else round(sim, 3),
                        problem=problem[:300],
                        solution=solution[:500],
                        service_id=item.get("service_id"),
                        similarity_pct=sim,
                    )
                )

        return ResponseContext(
            decision_id=decision_id,
            decision_version=decision_version,
            scenario_key=scenario_key,
            scenario_version=scenario_version,
            outcome_kind=outcome_kind,
            plan_phase=plan_phase,
            policy_text=policy_comment,
            allowed_evidence_refs=evidence_refs,
            confirmed_facts=safe_facts,
            public_plan_steps=public_steps,
            rag_candidates=rag_refs,
        )

    @staticmethod
    def build_prompt_variants(
        context: ResponseContext,
        tone: Literal["default", "concise", "detailed", "regulatory"] = "default",
    ) -> list[PromptVariant]:
        """
        Формирует Capacity-Aware варианты промпта:
        - local: не более AI_OLLAMA_MAX_RAG_MATCHES (по умолчанию 1) прецедента
        - cloud: не более AI_CLOUD_MAX_RAG_MATCHES (по умолчанию 3) прецедентов
        """
        system_prompt = (
            "Ты профессиональный дежурный инженер первой линии Helpdesk. "
            "Твоя задача — сформулировать безопасный ответ заявителю строго на основе согласованного текста регламента и подтвержденных фактов. "
            "Запрещено выдумывать непроверенные действия, системные команды или утверждать, что неподтвержденная проверка уже выполнена."
        )

        tone_instruction = "Сформулируй профессиональный, вежливый и четкий ответ заявителю."
        if tone == "concise":
            tone_instruction = (
                "Сформулируй предельно лаконичный ответ в 1-2 предложениях. "
                "Обязательно сохрани ключевой вопрос или запрос данных, если требуется уточнение."
            )
        elif tone == "detailed":
            tone_instruction = (
                "Сформулируй развернутый ответ в виде 2-4 последовательных шагов для заявителя. "
                "Не перекладывай на заявителя привилегированные действия (правка реестра, переустановка ОС, замена комплектующих)."
            )

        # 1. Local variant (1 RAG match max)
        local_rag = context.rag_candidates[: settings.AI_OLLAMA_MAX_RAG_MATCHES]
        local_rag_refs = [f"ticket:{r.task_id}" for r in local_rag]
        local_payload = {
            "policy_text": context.policy_text,
            "outcome_kind": context.outcome_kind,
            "plan_phase": context.plan_phase,
            "confirmed_facts": context.confirmed_facts,
            "public_steps": [s["title"] for s in context.public_plan_steps],
            "precedents": [
                {"task_id": r.task_id, "problem": r.problem, "solution": r.solution}
                for r in local_rag
            ],
            "tone_instruction": tone_instruction,
            "constraints": [
                "Rewrite only the supplied policy and facts in natural Russian",
                "Do not claim that an action was completed or checked without evidence",
                "Do not add ungrounded identifiers, technical values, or instructions",
                "Return only JSON matching the schema",
            ],
        }
        local_variant = PromptVariant(
            profile="local",
            prompt=json.dumps(local_payload, ensure_ascii=False),
            system_prompt=system_prompt,
            rag_refs=local_rag_refs,
            max_tokens=settings.AI_OLLAMA_MAX_TOKENS,
        )

        # 2. Cloud variant (up to AI_CLOUD_MAX_RAG_MATCHES matches)
        cloud_rag = context.rag_candidates[: settings.AI_CLOUD_MAX_RAG_MATCHES]
        cloud_rag_refs = [f"ticket:{r.task_id}" for r in cloud_rag]
        cloud_payload = {
            "policy_text": context.policy_text,
            "outcome_kind": context.outcome_kind,
            "plan_phase": context.plan_phase,
            "confirmed_facts": context.confirmed_facts,
            "public_steps": [s["title"] for s in context.public_plan_steps],
            "precedents": [
                {"task_id": r.task_id, "problem": r.problem, "solution": r.solution}
                for r in cloud_rag
            ],
            "tone_instruction": tone_instruction,
            "constraints": [
                "Rewrite only the supplied policy and facts in natural Russian",
                "Do not claim that an action was completed or checked without evidence",
                "Do not add ungrounded identifiers, technical values, or instructions",
                "Return only JSON matching the schema",
            ],
        }
        cloud_variant = PromptVariant(
            profile="cloud",
            prompt=json.dumps(cloud_payload, ensure_ascii=False),
            system_prompt=system_prompt,
            rag_refs=cloud_rag_refs,
            max_tokens=settings.AI_CLOUD_MAX_TOKENS,
        )

        return [local_variant, cloud_variant]
