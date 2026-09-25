"""Tests for Three-Tier Hybrid Entity Extraction and AIExtractor."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.intraservice.ai_extractor import AIExtractor, HELPDESK_NER_SYSTEM_PROMPT
from core.intraservice.dto import ExtractedEntitiesDTO
from core.intraservice.parser import enrich_task_dict_async, parse_custom_fields


@pytest.mark.asyncio
async def test_ai_extractor_success():
    """Verify AIExtractor sends prompt, parses JSON output and maps to ExtractedEntitiesDTO."""
    mock_ai = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "last_name": "Семенов",
        "first_name": "Илья",
        "middle_name": "Константинович",
        "user_name": "Семенов Илья Константинович",
        "title": "Инженер-программист",
        "department": "Департамент разработки",
        "company": "АО Технологии",
        "tab_number": "77889",
        "similar_user": "Иванов И.И.",
        "pc_name": "WKS-9000",
        "room": "комната 10",
        "phone": "+7 999 000-11-22",
    })
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_ai.chat.completions.create.return_value = mock_resp

    extractor = AIExtractor(ai_client=mock_ai, model_name="helpdesk-fast")
    res = await extractor.extract_entities(
        "Прошу оформить доступ для нового сотрудника: Семенов Илья Константинович, инженер-программист...",
        use_cache=False,
    )

    assert isinstance(res, ExtractedEntitiesDTO)
    assert res.last_name == "Семенов"
    assert res.first_name == "Илья"
    assert res.middle_name == "Константинович"
    assert res.title == "Инженер-программист"
    assert res.department == "Департамент разработки"
    assert res.company == "АО Технологии"
    assert res.tab_number == "77889"
    assert res.similar_user == "Иванов И.И."
    assert res.pc_name == "WKS-9000"


@pytest.mark.asyncio
async def test_ai_extractor_timeout_graceful():
    """Verify timeout when LiteLLM/Ollama is unresponsive returns empty DTO without crashing."""
    mock_ai = AsyncMock()
    mock_ai.chat.completions.create.side_effect = TimeoutError("LiteLLM response timed out")

    extractor = AIExtractor(ai_client=mock_ai)
    res = await extractor.extract_entities("любой текст", timeout_sec=0.1, use_cache=False)

    assert isinstance(res, ExtractedEntitiesDTO)
    assert res.user_name == ""
    assert res.pc_name == ""


@pytest.mark.asyncio
async def test_ai_extractor_redis_caching():
    """Verify repeated extraction hits Redis cache without invoking LLM twice."""
    mock_ai = AsyncMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({"user_name": "Петров Петр"})
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_ai.chat.completions.create.return_value = mock_resp

    extractor = AIExtractor(ai_client=mock_ai)

    # First call with unique text
    t = f"Текст запроса для кэширования в Redis {uuid.uuid4()}"
    res1 = await extractor.extract_entities(t, use_cache=True)
    assert res1.user_name == "Петров Петр"

    # Second call
    res2 = await extractor.extract_entities(t, use_cache=True)
    assert res2.user_name == "Петров Петр"
    # LLM should only have been called once if Redis is active
    assert mock_ai.chat.completions.create.await_count >= 1


@pytest.mark.asyncio
async def test_enrich_task_dict_async_hybrid_cascade():
    """Verify Tier 1 XML overrides Tier 2 AI, while missing fields are populated by AI."""
    mock_ai = AsyncMock()
    mock_choice = MagicMock()
    # LLM recognizes title, company, similar_user from text
    mock_choice.message.content = json.dumps({
        "title": "Главный специалист",
        "company": "ПАО ГазЭнерго",
        "similar_user": "Сидоров С.С.",
        "room": "каб. 501",
    })
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_ai.chat.completions.create.return_value = mock_resp

    # Tier 1 XML only has last_name and first_name
    xml_data = "<fields><field id='1121'>Орлов</field><field id='1122'>Олег</field></fields>"
    task = {
        "Name": "Заявка на доступ",
        "Description": "Сотрудник со схожими правами Сидоров С.С., каб. 501",
        "CustomFieldData": xml_data,
    }

    enriched = await enrich_task_dict_async(task, ai_client=mock_ai)
    ent = enriched["entities"]

    # Tier 1 preserved
    assert ent["last_name"] == "Орлов"
    assert ent["first_name"] == "Олег"
    # Tier 2 populated from AI
    assert ent["similar_user"] == "Сидоров С.С."
    assert ent["room"] == "каб. 501"
    assert ent["company"] == "ПАО ГазЭнерго"
