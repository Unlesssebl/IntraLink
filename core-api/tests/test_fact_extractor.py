import json
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.services.ai.hub import ai_hub
from app.services.fact_extractor import enrich_task_with_extracted_facts


@pytest.mark.asyncio
async def test_llm_only_fills_missing_facts_with_grounded_schema():
    task = {
        "Name": "Оформление доступа",
        "Description": "Фамилия Иванов, имя Иван, должность Инженер, отдел ИТ, организация Интра",
        "_field_meta": {"raw": {}},
    }
    extracted = {
        "schema_version": 1,
        "person": {
            "surname": "Иванов",
            "name": "Иван",
            "title": "Инженер",
            "department": "ИТ",
            "company": "Интра",
        },
        "evidence": [
            {
                "source": "llm",
                "field": "surname",
                "code": "quoted",
                "span": "Фамилия Иванов",
            }
        ],
        "ambiguities": [],
    }
    with (
        patch.object(settings, "LLM_FACT_EXTRACTION_MODE", "enabled"),
        patch.object(
            ai_hub,
            "generate_ollama_completion",
            new=AsyncMock(return_value=json.dumps(extracted, ensure_ascii=False)),
        ) as completion,
    ):
        enriched = await enrich_task_with_extracted_facts(task)

    assert enriched["_extracted_person"]["surname"] == "Иванов"
    assert enriched["_llm_fact_extraction"]["accepted"] is True
    assert (
        completion.await_args.kwargs["response_schema"]["additionalProperties"] is False
    )


@pytest.mark.asyncio
async def test_llm_never_repairs_explicit_invalid_identity():
    task = {
        "Name": "Создание пользователя",
        "Description": "",
        "_field_meta": {"raw": {"1057": "test", "1058": "тест"}},
    }
    completion = AsyncMock()
    with (
        patch.object(settings, "LLM_FACT_EXTRACTION_MODE", "enabled"),
        patch.object(ai_hub, "generate_ollama_completion", new=completion),
    ):
        enriched = await enrich_task_with_extracted_facts(task)
    assert enriched is task
    completion.assert_not_awaited()


@pytest.mark.asyncio
async def test_gemini_first_with_ollama_fallback():
    task = {
        "Id": 12345,
        "Name": "Настройка принтера",
        "Description": "Не печатает МФУ в 204 кабинете",
        "_field_meta": {"raw": {}},
    }
    extracted = {
        "schema_version": 1,
        "pc_name": "NTEMW0144",
        "printer_address": "10.244.15.55",
        "evidence": [
            {
                "source": "llm",
                "field": "printer_address",
                "code": "quoted",
                "span": "10.244.15.55",
            }
        ],
        "ambiguities": [],
    }

    gemini_mock = AsyncMock(side_effect=TimeoutError("Cloud timed out"))
    ollama_mock = AsyncMock(return_value=json.dumps(extracted, ensure_ascii=False))

    with (
        patch.object(settings, "LLM_FACT_EXTRACTION_MODE", "enabled"),
        patch.object(settings, "LLM_PROVIDER_PREFERENCE", "gemini_first"),
        patch.object(ai_hub, "generate_cloud_completion", new=gemini_mock),
        patch.object(ai_hub, "generate_ollama_completion", new=ollama_mock),
        patch("app.services.fact_extractor._fetch_from_cache", new=AsyncMock(return_value=None)),
        patch("app.services.fact_extractor._save_to_cache", new=AsyncMock()),
    ):
        enriched = await enrich_task_with_extracted_facts(task)

    gemini_mock.assert_awaited_once()
    ollama_mock.assert_awaited_once()
    assert enriched["_extracted_printer_address"] == "10.244.15.55"
    assert enriched["_extracted_pc_name"] == "NTEMW0144"


@pytest.mark.asyncio
async def test_extract_facts_from_comments_history():
    task = {
        "Id": 99999,
        "Name": "Проблема с ПК",
        "Description": "Ничего не работает",
        "_field_meta": {"raw": {}},
        "Comments": [
            {"Editor": "Специалист", "Created": "10:00", "Comments": "Уточните имя вашего компьютера"},
            {"Editor": "Заявитель", "Created": "10:05", "Comments": "Мой комп NTEMW0999, включил его в сеть"},
        ],
    }
    extracted = {
        "schema_version": 1,
        "pc_name": "NTEMW0999",
        "clarification_answer": "Мой комп NTEMW0999, включил его в сеть",
        "evidence": [
            {
                "source": "llm",
                "field": "pc_name",
                "code": "quoted",
                "span": "NTEMW0999",
            }
        ],
        "ambiguities": [],
    }

    with (
        patch.object(settings, "LLM_FACT_EXTRACTION_MODE", "enabled"),
        patch.object(settings, "LLM_PROVIDER_PREFERENCE", "gemini_first"),
        patch.object(ai_hub, "generate_cloud_completion", new=AsyncMock(return_value=json.dumps(extracted))),
        patch("app.services.fact_extractor._fetch_from_cache", new=AsyncMock(return_value=None)),
        patch("app.services.fact_extractor._save_to_cache", new=AsyncMock()),
    ):
        enriched = await enrich_task_with_extracted_facts(task)

    assert enriched["_extracted_pc_name"] == "NTEMW0999"
    assert enriched["_extracted_clarification_answer"] == "Мой комп NTEMW0999, включил его в сеть"
    assert enriched["_llm_fact_extraction"]["comments_count_analyzed"] == 2

