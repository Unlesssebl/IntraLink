"""Tests for UniversalIntentExtractor with strict Groundedness and Intent Detection."""

import pytest
from core.automation.clarification import (
    FactCorrectionProposal,
    UniversalIntentExtractor,
    extract_labeled_facts,
)
from core.automation.contracts import SnapshotComment
from core.automation.llm_transport import LLMTransport
from core.automation.workflows import FactNormalization, WorkflowFactSpec


class FakeLLMTransport(LLMTransport):
    def __init__(self, canned_response: str) -> None:
        self.canned_response = canned_response
        self.last_system_prompt: str | None = None
        self.last_payload: dict | None = None

    async def complete_json(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        payload: dict,
        timeout_seconds: float = 15.0,
    ) -> str:
        self.last_system_prompt = system_prompt
        self.last_payload = payload
        return self.canned_response


@pytest.fixture
def onboarding_specs():
    return (
        WorkflowFactSpec(
            key="last_name",
            label="Фамилия",
            synonyms=("фамилия", "last name"),
            normalization=FactNormalization.capitalize,
        ),
        WorkflowFactSpec(
            key="first_name",
            label="Имя",
            synonyms=("имя", "first name"),
            normalization=FactNormalization.capitalize,
        ),
        WorkflowFactSpec(
            key="middle_name",
            label="Отчество",
            synonyms=("отчество", "middle name"),
            normalization=FactNormalization.capitalize,
        ),
        WorkflowFactSpec(
            key="department",
            label="Подразделение",
            synonyms=("отдел", "подразделение"),
        ),
        WorkflowFactSpec(
            key="company",
            label="Организация",
            synonyms=("организация", "компания"),
        ),
    )


@pytest.fixture
def printer_specs():
    return (
        WorkflowFactSpec(
            key="pc_name",
            label="Имя компьютера",
            synonyms=("компьютер", "пк", "хост", "arm"),
            normalization=FactNormalization.upper,
        ),
        WorkflowFactSpec(
            key="printer_name_or_ip",
            label="Принтер / IP",
            synonyms=("принтер", "мфу", "печать"),
        ),
        WorkflowFactSpec(
            key="room",
            label="Кабинет",
            synonyms=("кабинет", "комната", "офис"),
        ),
    )


def test_extract_labeled_facts_generic(printer_specs):
    text = "Прошу подключить: Компьютер: arm-042\nПринтер: Canon MF440 в каб. 305"
    extracted = extract_labeled_facts(text, fact_specs=printer_specs)
    assert extracted["pc_name"]["value"] == "ARM-042"
    assert "Canon MF440" in extracted["printer_name_or_ip"]["value"]


def test_extract_labeled_facts_fio(onboarding_specs):
    text = "ахмедов ахмед ахмедович"
    extracted = extract_labeled_facts(text, fact_specs=onboarding_specs)
    assert extracted["last_name"]["value"] == "Ахмедов"
    assert extracted["first_name"]["value"] == "Ахмед"
    assert extracted["middle_name"]["value"] == "Ахмедович"


@pytest.mark.asyncio
async def test_detect_deterministic_correction(onboarding_specs):
    current = {
        "last_name": "Ахмедж",
        "first_name": "Ахмедж",
        "middle_name": "Ахмедж",
        "department": "АСУ",
    }
    comments = [
        SnapshotComment(
            id=101,
            text="Фамилия: Ахмедов",
            author_name="Ален Беликов",
            editor_id=8664,
            is_private=False,
        )
    ]
    extractor = UniversalIntentExtractor(FakeLLMTransport("{}"))
    proposals, diag = await extractor.detect_corrections_with_trace(
        comments,
        current,
        onboarding_specs,
        applicant_id=8664,
        bot_user_id=10502,
    )
    assert len(proposals) == 1
    assert proposals[0].param_key == "last_name"
    assert proposals[0].old_value == "Ахмедж"
    assert proposals[0].new_value == "Ахмедов"
    assert proposals[0].comment_id == 101


@pytest.mark.asyncio
async def test_detect_llm_correction_with_groundedness(onboarding_specs):
    current = {
        "last_name": "Ахмедж",
        "first_name": "Ахмедж",
        "middle_name": "Ахмедж",
        "department": "АСУ",
    }
    comments = [
        SnapshotComment(
            id=102,
            text="Опечатался в заявке, верные ФИО: Ахмедов Ахмед Ахмедович",
            author_name="Ален Беликов",
            editor_id=8664,
            is_private=False,
        )
    ]
    llm_json = """{
        "corrections": [
            {
                "param_key": "last_name",
                "old_value": "Ахмедж",
                "new_value": "Ахмедов",
                "text_span": "верные ФИО: Ахмедов Ахмед Ахмедович",
                "reason": "опечатка заявителя"
            },
            {
                "param_key": "first_name",
                "old_value": "Ахмедж",
                "new_value": "Ахмед",
                "text_span": "верные ФИО: Ахмедов Ахмед Ахмедович",
                "reason": "опечатка заявителя"
            }
        ]
    }"""
    extractor = UniversalIntentExtractor(FakeLLMTransport(llm_json))
    proposals, diag = await extractor.detect_corrections_with_trace(
        comments,
        current,
        onboarding_specs,
        applicant_id=8664,
        bot_user_id=10502,
    )
    assert len(proposals) == 2
    by_key = {p.param_key: p for p in proposals}
    assert by_key["last_name"].new_value == "Ахмедов"
    assert by_key["first_name"].new_value == "Ахмед"
    assert diag["llm_used"] is True


@pytest.mark.asyncio
async def test_detect_llm_ungrounded_rejection(onboarding_specs):
    current = {"last_name": "Ахмедж"}
    comments = [
        SnapshotComment(
            id=103,
            text="Здравствуйте, прошу ускорить",
            author_name="Ален Беликов",
            editor_id=8664,
            is_private=False,
        )
    ]
    # LLM hallucinates a new value that is NOT in text_span
    llm_json = """{
        "corrections": [
            {
                "param_key": "last_name",
                "old_value": "Ахмедж",
                "new_value": "Иванов",
                "text_span": "прошу ускорить",
                "reason": "галлюцинация"
            }
        ]
    }"""
    extractor = UniversalIntentExtractor(FakeLLMTransport(llm_json))
    proposals, diag = await extractor.detect_corrections_with_trace(
        comments,
        current,
        onboarding_specs,
        applicant_id=8664,
        bot_user_id=10502,
    )
    # Strictly rejected because 'Иванов' is not in text_span
    assert len(proposals) == 0


@pytest.mark.asyncio
async def test_detect_casefold_groundedness_with_normalization(onboarding_specs):
    current = {"last_name": "Ахмедж"}
    comments = [
        SnapshotComment(
            id=104,
            text="верная фамилия - ахмедов",
            author_name="Ален Беликов",
            editor_id=8664,
            is_private=False,
        )
    ]
    llm_json = """{
        "corrections": [
            {
                "param_key": "last_name",
                "old_value": "Ахмедж",
                "new_value": "ахмедов",
                "text_span": "верная фамилия - ахмедов",
                "reason": "уточнение"
            }
        ]
    }"""
    extractor = UniversalIntentExtractor(FakeLLMTransport(llm_json))
    proposals, diag = await extractor.detect_corrections_with_trace(
        comments,
        current,
        onboarding_specs,
        applicant_id=8664,
        bot_user_id=10502,
    )
    assert len(proposals) == 1
    # Normalized by FactNormalization.capitalize
    assert proposals[0].new_value == "Ахмедов"
