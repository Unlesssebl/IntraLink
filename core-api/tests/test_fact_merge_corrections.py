import pytest

from shared.domain import (
    FactObservation,
    FactSource,
    FactState,
)
from app.services.facts.merger import merge_observations
from app.services.scenarios.builtin import _printer_install_match
from app.services.scenarios import ScenarioContext


def _obs(key: str, value: str, source: FactSource, ref: str, state: FactState = FactState.VALID, metadata: dict | None = None) -> FactObservation:
    return FactObservation(
        key=key,
        value=value,
        state=state,
        source=source,
        source_ref=ref,
        evidence_span=value if source in {FactSource.COMMENT, FactSource.PARSER, FactSource.LLM} else None,
        metadata=metadata or {},
    )


def test_applicant_correction_supersedes_valid_structured_field():
    obs = [
        _obs("pc_name", "ZTE1000", FactSource.STRUCTURED_FIELD, "field:pc"),
        _obs(
            "pc_name",
            "ZTE2000",
            FactSource.COMMENT,
            "comment:1:pc_name",
            metadata={"is_correction": True, "comment_text": "Ошибся, мой ПК ZTE2000"},
        ),
    ]
    facts = merge_observations(obs)
    assert facts.valid_value("pc_name") == "ZTE2000"


def test_applicant_comment_supersedes_invalid_structured_field():
    obs = [
        _obs("pc_name", "нет номера", FactSource.STRUCTURED_FIELD, "field:pc", state=FactState.INVALID),
        _obs(
            "pc_name",
            "ZTE3000",
            FactSource.COMMENT,
            "comment:2:pc_name",
            metadata={"comment_text": "Мой компьютер ZTE3000"},
        ),
    ]
    facts = merge_observations(obs)
    assert facts.valid_value("pc_name") == "ZTE3000"


def test_operator_override_beats_applicant_correction():
    obs = [
        _obs("pc_name", "ZTE1000", FactSource.STRUCTURED_FIELD, "field:pc"),
        _obs(
            "pc_name",
            "ZTE2000",
            FactSource.COMMENT,
            "comment:1:pc_name",
            metadata={"is_correction": True},
        ),
        _obs("pc_name", "ZTE9999", FactSource.OPERATOR, "operator:correction"),
    ]
    facts = merge_observations(obs)
    assert facts.valid_value("pc_name") == "ZTE9999"


def test_printer_install_intent_classes():
    def _make_context(text: str) -> ScenarioContext:
        facts = merge_observations([
            _obs("subject", text, FactSource.STRUCTURED_FIELD, "ticket:subject"),
            _obs("description", text, FactSource.STRUCTURED_FIELD, "ticket:description"),
        ])
        return ScenarioContext(task={"Id": 1, "Name": text, "Description": text}, facts=facts)

    # 1. Action intent
    matched, conf, reason = _printer_install_match(_make_context("Прошу установить сетевой принтер"))
    assert matched is True
    assert conf > 0.9

    # 2. Completed intent (should NOT trigger install)
    matched, _, reason = _printer_install_match(_make_context("Принтер уже установлен, но нужен пароль"))
    assert matched is False
    assert reason == "intent_completed"

    # 3. Negation intent (should NOT trigger install)
    matched, _, reason = _printer_install_match(_make_context("Не нужно устанавливать принтер, заявка по ошибке"))
    assert matched is False
    assert reason == "intent_negated"

    # 4. Symptom intent (should NOT trigger install unless explicit reinstall)
    matched, _, reason = _printer_install_match(_make_context("Принтер не печатает, зажевало бумагу"))
    assert matched is False
    assert "intent_symptom" in reason

    # Reinstall permitted
    matched, _, _ = _printer_install_match(_make_context("Принтер не печатает, требуется переустановка"))
    assert matched is True
