"""ResponseVariantService: on-demand response tone synthesis and append-only persistence."""

from __future__ import annotations

import logging
from typing import Any, Literal
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import DecisionRecord, DecisionResponseVariant
from app.services.ai.hub import ai_hub
from app.services.ai.schemas import (
    InferencePurpose,
    RoutedInferenceRequest,
    RoutingMetadata,
)
from app.services.response_context import ResponseContextBuilder
from app.services.response_guard import GeneratedResponse, guarded_response, validate_response
from shared.domain import DecisionResponse, ResponseProvenance


logger = logging.getLogger("core_api.response_variants")


class ResponseVariantService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate_or_get_variant(
        self,
        *,
        task_id: int,
        decision_id: str,
        decision_version: int,
        tone: Literal["default", "concise", "detailed", "regulatory"],
        actor: str = "system",
        regenerate: bool = False,
        service_id: int | None = None,
    ) -> DecisionResponseVariant:
        dec_uuid = uuid.UUID(decision_id) if isinstance(decision_id, str) else decision_id

        # 1. Поиск уже существующего варианта при regenerate=False
        if not regenerate:
            existing = await self.db.scalar(
                select(DecisionResponseVariant)
                .where(
                    DecisionResponseVariant.decision_id == dec_uuid,
                    DecisionResponseVariant.decision_version == decision_version,
                    DecisionResponseVariant.tone == tone,
                    DecisionResponseVariant.is_active.is_(True),
                )
                .order_by(DecisionResponseVariant.created_at.desc())
                .limit(1)
            )
            if existing:
                return existing

        # 2. Проверка существования решения и валидация актуальности
        decision_record = await self.db.scalar(
            select(DecisionRecord)
            .where(
                DecisionRecord.id == dec_uuid,
                DecisionRecord.version == decision_version,
            )
            .limit(1)
        )
        if not decision_record:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="decision_stale",
            )

        envelope = decision_record.envelope_json or {}
        policy = envelope.get("policy") or {}
        policy_comment = str(policy.get("comment") or "").strip()
        scenario_key = str(envelope.get("scenario_key") or "general")
        scenario_version = int(envelope.get("scenario_version") or 1)
        outcome = envelope.get("outcome") or {}
        outcome_kind = str(outcome.get("kind") or "manual_review")
        facts_summary = envelope.get("facts_summary") or {}
        candidate_list = envelope.get("candidates") or []
        evidence_refs: list[str] = []
        if candidate_list:
            evidence_refs = candidate_list[0].get("evidence_refs") or []

        execution_plan = envelope.get("execution_plan")

        # 3. Генерация ответа в зависимости от запрошенного тона
        if tone == "regulatory":
            # РЕГЛАМЕНТ: Строго детерминированный шаблон без обращения к AI
            provenance = ResponseProvenance(
                source="template",
                actual_backend="template",
                tone="regulatory",
                circuit="unknown",
                context_profile="none",
                fallback_used=False,
                rag_candidate_count=0,
                rag_used_count=0,
            )
            response = guarded_response(
                generated=None,
                template_text=policy_comment,
                allowed_evidence_refs=evidence_refs,
                facts_summary=facts_summary,
                provenance=provenance,
            )
        else:
            # ДРУГИЕ ТОНАЛЬНОСТИ: capacity-aware синтез
            purpose_map = {
                "default": InferencePurpose.RESPONSE_DEFAULT,
                "concise": InferencePurpose.RESPONSE_CONCISE,
                "detailed": InferencePurpose.RESPONSE_DETAILED,
            }
            purpose = purpose_map.get(tone, InferencePurpose.RESPONSE_DEFAULT)

            response_ctx = ResponseContextBuilder.build_context(
                decision_id=str(dec_uuid),
                decision_version=decision_version,
                scenario_key=scenario_key,
                scenario_version=scenario_version,
                outcome_kind=outcome_kind,
                policy_comment=policy_comment,
                facts_summary=facts_summary,
                evidence_refs=evidence_refs,
                plan=execution_plan,
            )

            prompt_variants = ResponseContextBuilder.build_prompt_variants(
                response_ctx, tone=tone
            )
            primary = prompt_variants[1] if len(prompt_variants) > 1 else prompt_variants[0]

            generated = None
            provenance: ResponseProvenance | None = None
            try:
                inference_resp = await ai_hub.dispatch_routed_inference(
                    RoutedInferenceRequest(
                        prompt=primary.prompt,
                        system_prompt=primary.system_prompt,
                        metadata=RoutingMetadata(service_id=service_id),
                        temperature=0.0,
                        max_tokens=primary.max_tokens,
                        response_schema=GeneratedResponse.model_json_schema(),
                        prompt_variants=prompt_variants,
                        purpose=purpose,
                    )
                )
                if inference_resp is not None:
                    circuit_val = (
                        inference_resp.circuit.value
                        if hasattr(inference_resp.circuit, "value")
                        else str(inference_resp.circuit)
                    )
                    provenance = ResponseProvenance(
                        source="llm" if not inference_resp.fallback_used else "fallback_template",
                        requested_backend=inference_resp.requested_backend,
                        actual_backend=inference_resp.actual_backend,
                        model_alias=inference_resp.model_alias,
                        resolved_model=inference_resp.resolved_model,
                        circuit=circuit_val if circuit_val in ("red", "yellow", "green") else "unknown",
                        context_profile=inference_resp.context_profile,
                        tone=tone,
                        fallback_used=inference_resp.fallback_used,
                        fallback_reason_code=inference_resp.fallback_reason_code,
                        rag_candidate_count=len(response_ctx.rag_candidates),
                        rag_used_count=len(inference_resp.rag_refs),
                        rag_refs=inference_resp.rag_refs,
                        attempts=inference_resp.attempts,
                        duration_ms=int(inference_resp.execution_time_ms),
                    )
                    try:
                        generated = GeneratedResponse.model_validate_json(inference_resp.text)
                    except Exception:
                        provenance.source = "fallback_template"
                        provenance.fallback_used = True
                        provenance.fallback_reason_code = "invalid_schema"
                else:
                    provenance = ResponseProvenance(
                        source="fallback_template",
                        tone=tone,
                        fallback_used=True,
                        fallback_reason_code="all_providers_failed",
                        rag_candidate_count=len(response_ctx.rag_candidates),
                    )
            except Exception as e:
                logger.warning("Tone generation failed for task %s (tone %s): %s", task_id, tone, e)
                provenance = ResponseProvenance(
                    source="fallback_template",
                    tone=tone,
                    fallback_used=True,
                    fallback_reason_code="all_providers_failed",
                    rag_candidate_count=len(response_ctx.rag_candidates),
                )

            response = guarded_response(
                generated=generated,
                template_text=policy_comment,
                allowed_evidence_refs=evidence_refs,
                facts_summary=facts_summary,
                provenance=provenance,
            )

        # 4. Сохранение варианта в append-only таблицу
        if regenerate:
            # Деактивируем предыдущие активные варианты для этого тона
            await self.db.execute(
                update(DecisionResponseVariant)
                .where(
                    DecisionResponseVariant.decision_id == dec_uuid,
                    DecisionResponseVariant.decision_version == decision_version,
                    DecisionResponseVariant.tone == tone,
                    DecisionResponseVariant.is_active.is_(True),
                )
                .values(is_active=False)
            )

        variant_record = DecisionResponseVariant(
            id=uuid.uuid4(),
            decision_id=dec_uuid,
            decision_version=decision_version,
            task_id=task_id,
            tone=tone,
            response_text=response.text,
            mode=response.mode,
            state=response.state,
            violations_json=response.violations,
            provenance_json=response.provenance.model_dump() if response.provenance else {},
            is_active=True,
            created_by=actor,
        )
        self.db.add(variant_record)
        await self.db.commit()
        await self.db.refresh(variant_record)
        return variant_record

    async def list_variants(
        self,
        *,
        decision_id: str,
        decision_version: int,
    ) -> list[DecisionResponseVariant]:
        dec_uuid = uuid.UUID(decision_id) if isinstance(decision_id, str) else decision_id
        result = await self.db.scalars(
            select(DecisionResponseVariant)
            .where(
                DecisionResponseVariant.decision_id == dec_uuid,
                DecisionResponseVariant.decision_version == decision_version,
                DecisionResponseVariant.is_active.is_(True),
            )
            .order_by(DecisionResponseVariant.created_at.asc())
        )
        return list(result.all())
