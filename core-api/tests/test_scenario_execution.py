from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from shared.domain import (
    ActionProposed,
    CandidateOutcome,
    CreateUserParameters,
    Evidence,
    FactBag,
    FactObservation,
    FactSource,
    FactState,
)

from app.database.db import AsyncSessionLocal, ResolutionPolicy, ResponseTemplate
from app.services.decision_compiler import DecisionCompiler
from app.services.facts import collect_structured, merge_observations
from app.services.facts.collectors import collect_deterministic
from app.services.scenario_decision import ScenarioDecisionService
from app.services.scenarios import ScenarioContext, ScenarioRegistry
from app.services.scenario_orchestrator import ticket_event_key


def _observation(
    key: str,
    value: str,
    source: FactSource,
    ref: str,
    *,
    state: FactState = FactState.VALID,
) -> FactObservation:
    return FactObservation(
        key=key,
        value=value,
        state=state,
        source=source,
        source_ref=ref,
        evidence_span=value if source in {FactSource.COMMENT, FactSource.PARSER, FactSource.LLM} else None,
    )


def test_fact_merge_uses_source_precedence_and_comment_beats_parser():
    facts = merge_observations(
        [
            _observation("pc_name", "PC-OLD", FactSource.PARSER, "parser:1"),
            _observation("pc_name", "PC-NEW", FactSource.COMMENT, "comment:1"),
        ]
    )

    assert facts.valid_value("pc_name") == "PC-NEW"
    assert facts.facts["pc_name"].selected_source is FactSource.COMMENT


def test_invalid_structured_value_cannot_be_overridden_by_llm():
    facts = merge_observations(
        [
            _observation(
                "surname",
                "test",
                FactSource.STRUCTURED_FIELD,
                "field:1057",
                state=FactState.INVALID,
            ),
            _observation("surname", "Иванов", FactSource.LLM, "llm:surname"),
        ],
        include_shadow=True,
    )

    assert facts.facts["surname"].state is FactState.INVALID
    assert facts.facts["surname"].value == "test"


def test_latest_valid_operator_observation_wins():
    facts = merge_observations(
        [
            _observation("pc_name", "PC-OLD", FactSource.OPERATOR, "operator:first"),
            _observation("pc_name", "PC-NEW", FactSource.OPERATOR, "operator:second"),
        ]
    )

    assert facts.valid_value("pc_name") == "PC-NEW"
    assert facts.facts["pc_name"].selected_source_ref == "operator:second"


def test_expired_latest_operator_observation_does_not_hide_valid_value():
    valid = _observation("pc_name", "PC-VALID", FactSource.OPERATOR, "operator:first")
    expired = _observation("pc_name", "PC-EXPIRED", FactSource.OPERATOR, "operator:second")
    expired.expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

    facts = merge_observations([valid, expired])

    assert facts.valid_value("pc_name") == "PC-VALID"


def test_invalid_latest_operator_observation_does_not_hide_valid_value():
    valid = _observation("pc_name", "PC-VALID", FactSource.OPERATOR, "operator:first")
    invalid = _observation(
        "pc_name",
        "invalid host",
        FactSource.OPERATOR,
        "operator:second",
        state=FactState.INVALID,
    )

    facts = merge_observations([valid, invalid])

    assert facts.valid_value("pc_name") == "PC-VALID"


def test_different_comments_of_same_priority_remain_conflicting():
    facts = merge_observations(
        [
            _observation("pc_name", "PC-01", FactSource.COMMENT, "comment:1:pc_name"),
            _observation("pc_name", "PC-02", FactSource.COMMENT, "comment:2:pc_name"),
        ]
    )

    assert facts.facts["pc_name"].state is FactState.CONFLICTING


@pytest.mark.asyncio
async def test_comment_source_ref_is_stable_when_history_order_changes():
    first = {
        "Comment": "Подключить к PC-01",
        "Author": "user",
        "Created": "2026-09-08T10:00:00Z",
    }
    second = {
        "Comment": "Адрес 10.1.2.3",
        "Author": "user",
        "Created": "2026-09-08T10:01:00Z",
    }

    normal = await collect_deterministic({}, [first, second])
    reordered = await collect_deterministic({}, [second, first])

    assert {(item.key, item.source_ref) for item in normal} == {
        (item.key, item.source_ref) for item in reordered
    }


@pytest.mark.asyncio
async def test_explicit_observations_do_not_trigger_collection():
    async with AsyncSessionLocal() as db:
        service = ScenarioDecisionService(db)
        service.fact_planner.collect = AsyncMock(side_effect=AssertionError("unexpected collection"))
        await service.analyze(
            task={"Id": 1, "Name": "Обычная консультация"},
            observations=[
                _observation("task_id", "1", FactSource.STRUCTURED_FIELD, "ticket:task_id")
            ],
        )

    service.fact_planner.collect.assert_not_awaited()


def test_printer_installation_routes_only_explicit_install_intent():
    registry = ScenarioRegistry()
    facts = merge_observations(
        [
            _observation("pc_name", "PC-01", FactSource.STRUCTURED_FIELD, "field:pc"),
            _observation("printer_name", "HP LaserJet", FactSource.STRUCTURED_FIELD, "field:model"),
            _observation("printer_address", "10.1.2.3", FactSource.STRUCTURED_FIELD, "field:ip"),
        ]
    )

    install = registry.route(
        ScenarioContext(task={"ServiceId": 19, "Name": "Подключить принтер HP"}, facts=facts)
    )
    failure = registry.route(
        ScenarioContext(task={"ServiceId": 19, "Name": "Принтер не печатает"}, facts=facts)
    )
    reinstall = registry.route(
        ScenarioContext(task={"ServiceId": 19, "Name": "Переустановить принтер"}, facts=facts)
    )

    assert (install.definition.key, install.definition.version) == ("install_printer", 2)
    assert failure.definition.key != "install_printer"
    assert (reinstall.definition.key, reinstall.definition.version) == ("install_printer", 2)
    assert registry.get("install_printer", 1) is not None


def test_scenario_canary_is_stable_for_task_id():
    registry = ScenarioRegistry()
    first = registry.canary_selected(140479, 37)
    assert all(registry.canary_selected(140479, 37) is first for _ in range(10))


def test_ticket_event_key_deduplicates_same_delivery_and_changes_on_delta():
    task = {"Id": 140479, "Name": "Заявка"}
    first = ticket_event_key(task, [{"Comment": "уточнение"}], "comment_added")
    duplicate = ticket_event_key(task, [{"Comment": "уточнение"}], "comment_added")
    changed = ticket_event_key(task, [{"Comment": "новый ответ"}], "comment_added")

    assert first == duplicate
    assert first != changed


@pytest.mark.asyncio
async def test_rag_candidate_cannot_authorize_action():
    outcome = ActionProposed(
        rule_key="rag",
        rule_version="1",
        outcome_key="create_user_proposed",
        action="create_user",
        parameters=CreateUserParameters(surname="Иванов", name="Иван"),
        risk_level=2,
        evidence=[Evidence(source="rule", field="surname", code="claimed")],
    )
    candidate = CandidateOutcome(
        candidate_id="rag:1",
        source="rag",
        outcome=outcome,
        evidence_refs=["rag:document:1"],
        can_authorize_action=True,
    )

    with pytest.raises(ValueError, match="no_eligible_decision_candidate"):
        await DecisionCompiler(adjudicator=None).compile(
            scenario_key="create_user",
            scenario_version=1,
            scenario_risk=2,
            allowed_actions={"create_user"},
            facts=FactBag(),
            candidates=[candidate],
        )


@pytest.mark.asyncio
async def test_regression_140479_scenario_requires_clarification_and_no_action():
    task = {
        "Id": 140479,
        "ServiceId": 53,
        "Name": "Создание учетной записи",
        "Description": "Новый сотрудник",
        "_field_meta": {
            "raw": {"1057": "test", "1058": "тест", "1065": "-", "1064": "-"}
        },
    }
    observations = await collect_structured(task)

    async with AsyncSessionLocal() as db:
        template = ResponseTemplate(
            key="account_details_clarify",
            version=1,
            name="Уточнение реквизитов",
            template_text="Уточните: {{ invalid_fields }}",
            required_variables=["invalid_fields"],
            is_active=True,
            created_by="test",
        )
        db.add(template)
        await db.flush()
        db.add(
            ResolutionPolicy(
                outcome_key="account_details_invalid",
                version=1,
                outcome_kind="clarification",
                template_id=template.id,
                target_status_id=35,
                expenses=5,
                risk_level=0,
                requires_approval=False,
                is_active=True,
                created_by="test",
            )
        )
        await db.flush()
        envelope = await ScenarioDecisionService(db).analyze(
            task=task,
            observations=observations,
            fact_revision=1,
        )

    assert envelope.scenario_key == "create_user"
    assert envelope.outcome.kind == "clarification"
    assert envelope.outcome.outcome_key == "account_details_invalid"
    assert envelope.policy["status_id"] == 35
    assert not isinstance(envelope.outcome, ActionProposed)


@pytest.mark.asyncio
async def test_high_risk_compiler_never_calls_llm_adjudicator():
    called = False

    async def adjudicator(*_args):
        nonlocal called
        called = True
        return None

    task = {
        "Id": 7,
        "ServiceId": 53,
        "Name": "Создание учетной записи",
        "Description": "",
        "_field_meta": {
            "raw": {
                "1057": "Иванов",
                "1058": "Иван",
                "1065": "Инженер",
                "1064": "ИТ",
                "1074": "Интра",
            }
        },
    }
    observations = await collect_structured(task)
    async with AsyncSessionLocal() as db:
        service = ScenarioDecisionService(db)
        service.compiler = DecisionCompiler(adjudicator=adjudicator)
        envelope = await service.analyze(task=task, observations=observations)

    assert envelope.outcome.kind == "action"
    assert called is False
