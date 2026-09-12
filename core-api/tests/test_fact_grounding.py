import json
from unittest.mock import AsyncMock, patch
import pytest

from shared.domain import (
    Evidence,
    ExtractedTicketFacts,
    TextSourceFragment,
)
from app.config import settings
from app.services.ai.hub import ai_hub
from app.services.ai.schemas import RoutedInferenceResponse
from app.services.fact_extractor import (
    enrich_task_with_extracted_facts,
    validate_extracted_grounding,
)


def test_text_source_fragment_contract():
    text = "Прошу настроить принтер на рабочем месте ZTE1234."
    fragment = TextSourceFragment(
        field_name="Description",
        offset_start=41,
        offset_end=48,
        text="ZTE1234",
    )
    assert fragment.span() == (41, 48)
    assert fragment.matches_slice(text) is True

    # Bad offsets or text mismatch
    bad_fragment = TextSourceFragment(
        field_name="Description",
        offset_start=0,
        offset_end=7,
        text="ZTE1234",
    )
    assert bad_fragment.matches_slice(text) is False


def test_validate_extracted_grounding():
    full_text = "Настройте сетевой принтер 10.244.1.20 для бухгалтерии"

    valid_facts = ExtractedTicketFacts(
        schema_version=1,
        printer_address="10.244.1.20",
        evidence=[
            Evidence(
                source="llm",
                field="printer_address",
                code="quoted",
                span="10.244.1.20",
            )
        ],
    )
    ok, err = validate_extracted_grounding(valid_facts, full_text)
    assert ok is True
    assert err is None

    # Hallucinated span not present in text
    ungrounded_facts = ExtractedTicketFacts(
        schema_version=1,
        printer_address="10.244.1.20",
        evidence=[
            Evidence(
                source="llm",
                field="printer_address",
                code="quoted",
                span="192.168.1.99",
            )
        ],
    )
    ok_ungrounded, err_ungrounded = validate_extracted_grounding(ungrounded_facts, full_text)
    assert ok_ungrounded is False
    assert err_ungrounded == "ungrounded_evidence_span:printer_address"


@pytest.mark.asyncio
async def test_enrich_task_rejects_ungrounded_llm_facts():
    task = {
        "Name": "Заявка",
        "Description": "Ничего не указано",
        "_field_meta": {"raw": {}},
    }
    extracted = {
        "schema_version": 1,
        "pc_name": "ZTE9999",
        "evidence": [
            {
                "source": "llm",
                "field": "pc_name",
                "code": "quoted",
                "span": "ZTE9999",  # Not in task description!
            }
        ],
        "ambiguities": [],
    }
    mock_result = RoutedInferenceResponse(
        circuit="red",
        model="qwen",
        text=json.dumps(extracted, ensure_ascii=False),
        execution_time_ms=10.0,
    )
    with (
        patch.object(settings, "LLM_FACT_EXTRACTION_MODE", "enabled"),
        patch.object(settings, "LLM_PROVIDER_PREFERENCE", "ollama_only"),
        patch.object(
            ai_hub,
            "dispatch_routed_inference",
            new=AsyncMock(return_value=mock_result),
        ),
        patch("app.services.fact_extractor._fetch_from_cache", new=AsyncMock(return_value=None)),
        patch("app.services.fact_extractor._save_to_cache", new=AsyncMock()),
    ):
        enriched = await enrich_task_with_extracted_facts(task)

    assert enriched["_llm_fact_extraction"]["accepted"] is False
    assert "ungrounded_evidence_span:pc_name" in enriched["_llm_fact_extraction"]["reason"]
    assert "_extracted_pc_name" not in enriched
