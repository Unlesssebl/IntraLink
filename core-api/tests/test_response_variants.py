"""Unit and integration tests for ResponseVariantService, tones and cross-backend fallback."""

from unittest.mock import AsyncMock, patch
import uuid
import pytest
from sqlalchemy import select

from app.database.db import (
    AsyncSessionLocal,
    DecisionRecord,
    DecisionResponseVariant,
)
from app.services.ai.hub import ai_hub
from app.services.ai.schemas import (
    InferencePurpose,
    PromptVariant,
    RoutedInferenceRequest,
    RoutedInferenceResponse,
    RoutingMetadata,
)
from app.services.response_variant_service import ResponseVariantService


@pytest.mark.asyncio
async def test_regulatory_tone_is_deterministic_template():
    """Regulatory tone must be rendered deterministically from template without calling LLM."""
    dec_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        # Создаем базовое решение
        rec = DecisionRecord(
            id=dec_id,
            task_id=2001,
            version=1,
            analysis_kind="triage",
            status="finalized",
            outcome="grant_wlan",
            context_fingerprint="fp-2001",
            context_json={},
            created_by="system",
            envelope_json={
                "scenario_key": "wifi_access",
                "scenario_version": 1,
                "policy": {
                    "comment": "Доступ к беспроводной корпоративной сети WLAN-WORKNET успешно предоставлен.",
                    "status_id": 29,
                },
                "outcome": {"kind": "grant_wlan"},
                "facts_summary": {"pc_name": {"state": "valid", "value": "PC-FIN-01"}},
                "candidates": [{"evidence_refs": []}],
            },
        )
        db.add(rec)
        await db.commit()

        service = ResponseVariantService(db)

        # Мокаем dispatch_routed_inference и проверяем, что он НЕ вызывается
        with patch.object(ai_hub, "dispatch_routed_inference", new=AsyncMock()) as mock_dispatch:
            variant = await service.generate_or_get_variant(
                task_id=2001,
                decision_id=str(dec_id),
                decision_version=1,
                tone="regulatory",
                actor="test_user",
            )

            mock_dispatch.assert_not_called()
            assert variant.tone == "regulatory"
            assert variant.mode == "template"
            assert variant.is_active is True
            assert variant.decision_id == dec_id
            assert "WLAN-WORKNET" in variant.response_text
            assert variant.provenance_json.get("fallback_used") is False
            assert variant.provenance_json.get("actual_backend") == "template"


@pytest.mark.asyncio
async def test_llm_tones_generation_and_persistence():
    """Concise and detailed tones are generated via LLM, persisted and retrieved idempotently."""
    dec_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        rec = DecisionRecord(
            id=dec_id,
            task_id=2002,
            version=1,
            analysis_kind="triage",
            status="finalized",
            outcome="cache_cleanup",
            context_fingerprint="fp-2002",
            context_json={},
            created_by="system",
            envelope_json={
                "scenario_key": "1c_cache",
                "scenario_version": 1,
                "policy": {
                    "comment": "Выполните очистку локального кэша 1С.",
                    "status_id": 27,
                },
                "outcome": {"kind": "cache_cleanup"},
                "facts_summary": {},
                "candidates": [{"evidence_refs": []}],
            },
        )
        db.add(rec)
        await db.commit()

        service = ResponseVariantService(db)

        mock_llm_response = RoutedInferenceResponse(
            text='{"text": "Выполните сброс настроек приложения для устранения сбоя.", "used_evidence_refs": []}',
            circuit="green",
            model="gpt-4o-mini",
            actual_backend="litellm",
            requested_backend="litellm",
            model_alias="gpt-4o-mini",
            fallback_used=False,
            context_profile="cloud",
            rag_refs=["ticket:104"],
        )

        with patch.object(ai_hub, "dispatch_routed_inference", new=AsyncMock(return_value=mock_llm_response)) as mock_dispatch:
            # 1. Первая генерация тона concise
            variant1 = await service.generate_or_get_variant(
                task_id=2002,
                decision_id=str(dec_id),
                decision_version=1,
                tone="concise",
                actor="operator1",
            )

            assert mock_dispatch.call_count == 1
            assert variant1.tone == "concise"
            assert "Выполните сброс настроек" in variant1.response_text
            assert variant1.is_active is True
            assert variant1.provenance_json["model_alias"] == "gpt-4o-mini"

            # 2. Повторный запрос без regenerate=True возвращает закэшированный вариант
            variant2 = await service.generate_or_get_variant(
                task_id=2002,
                decision_id=str(dec_id),
                decision_version=1,
                tone="concise",
                regenerate=False,
            )

            # mock_dispatch НЕ должен вызываться повторно
            assert mock_dispatch.call_count == 1
            assert variant2.id == variant1.id

            # 3. Запрос с regenerate=True создает новую запись и деактивирует старую
            new_llm_response = RoutedInferenceResponse(
                text='{"text": "Обновленный краткий ответ: перезапустите приложение.", "used_evidence_refs": []}',
                circuit="green",
                model="gpt-4o-mini",
                actual_backend="litellm",
                requested_backend="litellm",
                model_alias="gpt-4o-mini",
                fallback_used=False,
                context_profile="cloud",
            )
            mock_dispatch.return_value = new_llm_response

            variant3 = await service.generate_or_get_variant(
                task_id=2002,
                decision_id=str(dec_id),
                decision_version=1,
                tone="concise",
                regenerate=True,
                actor="operator2",
            )

            assert mock_dispatch.call_count == 2
            assert variant3.id != variant1.id
            assert variant3.is_active is True
            assert "Обновленный краткий ответ" in variant3.response_text

            # Проверяем, что variant1 стал неактивным в БД
            old_variant = await db.scalar(
                select(DecisionResponseVariant).where(DecisionResponseVariant.id == variant1.id)
            )
            assert old_variant.is_active is False


@pytest.mark.asyncio
async def test_cross_backend_fallback_on_cloud_failure():
    """When primary cloud backend fails, ai_hub automatically falls back to local Ollama."""
    request = RoutedInferenceRequest(
        prompt="Cloud prompt",
        purpose=InferencePurpose.RESPONSE_CONCISE,
        prompt_variants=[
            PromptVariant(profile="local", prompt="Local prompt", max_tokens=256, rag_refs=["ticket:101"]),
            PromptVariant(profile="cloud", prompt="Cloud prompt", max_tokens=512, rag_refs=["ticket:101", "ticket:102"]),
        ],
        preferred_backend="litellm",
        bypass_cache=True,
    )

    async def mock_cloud(*args, **kwargs):
        return None

    async def mock_ollama(*args, **kwargs):
        return '{"text": "Локальный ответ Ollama", "used_evidence_refs": []}'

    with patch.object(ai_hub, "generate_cloud_completion", new=mock_cloud), \
         patch.object(ai_hub, "generate_ollama_completion", new=mock_ollama):
        resp = await ai_hub.dispatch_routed_inference(request)

        assert resp.fallback_used is True
        assert resp.actual_backend == "ollama"
        assert resp.requested_backend == "litellm_gemini"
        assert resp.fallback_reason_code == "cloud_unavailable"
        assert resp.context_profile == "local"
        assert len(resp.rag_refs) == 1
        assert "Локальный ответ Ollama" in resp.text
        assert len(resp.attempts) == 2


@pytest.mark.asyncio
async def test_list_variants_returns_active():
    """list_variants returns all active variants for decision."""
    dec_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        rec = DecisionRecord(
            id=dec_id,
            task_id=2003,
            version=1,
            analysis_kind="triage",
            status="finalized",
            outcome="test",
            context_fingerprint="fp-2003",
            context_json={},
            created_by="system",
            envelope_json={
                "scenario_key": "test_scenario",
                "scenario_version": 1,
                "policy": {"comment": "Тестовый регламент", "status_id": 27},
                "outcome": {"kind": "test"},
                "facts_summary": {},
                "candidates": [],
            },
        )
        db.add(rec)
        await db.commit()

        service = ResponseVariantService(db)

        # Создаем regulatory
        await service.generate_or_get_variant(
            task_id=2003,
            decision_id=str(dec_id),
            decision_version=1,
            tone="regulatory",
        )

        variants = await service.list_variants(
            decision_id=str(dec_id),
            decision_version=1,
        )

        assert len(variants) == 1
        assert variants[0].tone == "regulatory"
        assert variants[0].is_active is True
