"""Tests for resolution contract completeness, policy-template integrity, and fail-closed gates."""

from __future__ import annotations

import os
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.db import ResolutionPolicy, ResponseTemplate
from app.services.decision_compiler import DecisionCompiler
from app.services.resolution_service import render_response_template, template_variables
from app.services.response_guard import EMERGENCY_DRAFT_TEXT, guarded_response
from app.services.scenario_decision import ScenarioDecisionService
from app.services.scenarios.builtin import built_in_scenarios
from shared.domain import (
    CandidateOutcome,
    ClarificationRequired,
    FactBag,
)

POSTGRES_TEST_URL = os.environ.get(
    "POSTGRES_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/intraservice",
)


@pytest_asyncio.fixture
async def pg_session():
    """Сессия к реальной мигрированной PostgreSQL базе данных."""
    engine = create_async_engine(POSTGRES_TEST_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_declared_scenario_outcomes_have_active_policies(pg_session: AsyncSession):
    """Проверяет, что все объявленные исходы встроенных сценариев обеспечены активными политиками и шаблонами в БД."""
    scenarios = built_in_scenarios()
    required_outcomes: dict[str, str] = {}

    for scenario in scenarios:
        defn = scenario.definition
        if defn.clarification_outcome_key:
            required_outcomes[defn.clarification_outcome_key] = "clarification"
        if defn.success_outcome_key:
            required_outcomes[defn.success_outcome_key] = "resolution"

    # Специфические исходы, порождаемые правилами сценариев
    required_outcomes["in_work_standard"] = "resolution"
    required_outcomes["wrong_service"] = "resolution"
    required_outcomes["peripheral_clarify"] = "clarification"

    for outcome_key, expected_kind in required_outcomes.items():
        result = await pg_session.execute(
            select(ResolutionPolicy, ResponseTemplate)
            .join(ResponseTemplate, ResolutionPolicy.template_id == ResponseTemplate.id, isouter=True)
            .where(
                ResolutionPolicy.outcome_key == outcome_key,
                ResolutionPolicy.is_active.is_(True),
            )
        )
        rows = result.all()
        assert len(rows) == 1, (
            f"Исход '{outcome_key}' должен иметь ровно одну активную политику в БД, найдено: {len(rows)}"
        )
        policy, template = rows[0]
        assert policy.outcome_kind == expected_kind, (
            f"Несоответствие outcome_kind для '{outcome_key}': ожидался {expected_kind}, получен {policy.outcome_kind}"
        )
        assert template is not None, f"Политика '{outcome_key}' не имеет связанного шаблона"
        assert template.is_active is True, f"Шаблон '{template.key}' для политики '{outcome_key}' не активен"


@pytest.mark.asyncio
async def test_response_template_renders_valid_context(pg_session: AsyncSession):
    """Проверяет, что все активные шаблоны в БД корректно компилируются и рендерятся без пустых значений."""
    result = await pg_session.execute(select(ResponseTemplate).where(ResponseTemplate.is_active.is_(True)))
    templates = result.scalars().all()
    assert len(templates) > 0

    defaults = {
        "target_service": "1С:Предприятие",
        "target_service_name": "1С:Предприятие",
        "solution": "Перезапуск службы печати Spooler",
        "pc_name": "NTEMW0123",
        "user_fio": "Иванов Иван Иванович",
        "field": "инвентарный номер",
        "invalid_fields": "инвентарный номер оборудования",
        "printer_name": "HP LaserJet Pro 400",
        "printer_address": "192.168.1.120",
        "master_task_id": "140100",
        "occupied_user": "Петров Петр Петрович",
    }

    for tpl in templates:
        declared = set(tpl.required_variables or template_variables(tpl.template_text))
        ctx = {var: defaults.get(var, f"значение_{var}") for var in declared}
        rendered = render_response_template(tpl, ctx)
        assert rendered, f"Рендеринг шаблона '{tpl.key}' вернул пустую строку"
        assert "{{" not in rendered, f"Шаблон '{tpl.key}' оставил неразрешенные теги Jinja"
        assert len(rendered) <= 8000, f"Шаблон '{tpl.key}' превысил лимит длины"


@pytest.mark.asyncio
async def test_missing_or_invalid_policy_yields_emergency_draft_and_blocks_gates():
    """При недоступности или сбое политики отдается нейтральный черновик, а побочные эффекты блокируются fail-closed."""
    # 1. Guard возвращает аварийный текст при пустом шаблоне
    resp = guarded_response(
        generated=None,
        template_text="",
        allowed_evidence_refs=[],
        facts_summary={},
    )
    assert resp.text == EMERGENCY_DRAFT_TEXT
    assert resp.mode == "emergency_draft"
    assert resp.state == "invalid"
    assert len(resp.violations) > 0

    # 2. DecisionCompiler блокирует отправку и исполнение при ошибке политики
    compiler = DecisionCompiler()
    candidate = CandidateOutcome(
        candidate_id="test:outcome",
        source="rule",
        outcome=ClarificationRequired(
            rule_key="test.rule",
            rule_version="1",
            outcome_key="non_existent_policy",
            missing_fields=["pc_name"],
        ),
        evidence_refs=[],
    )
    envelope = await compiler.compile(
        scenario_key="test_scenario",
        scenario_version=1,
        scenario_risk=0,
        allowed_actions={"apply_triage"},
        facts=FactBag(),
        candidates=[candidate],
        policy={
            "resolution_error": "required_resolution_policy_unavailable:non_existent_policy",
            "resolution_error_code": "required_resolution_policy_unavailable",
        },
        response=resp,
    )

    assert envelope.gates.can_send_response is False
    assert envelope.gates.can_execute_action is False
    assert envelope.analysis_state == "manual_review"
    assert envelope.status == "manual_review"
    assert "required_resolution_policy_unavailable" in envelope.gates.blocked_reasons
    assert "response_invalid" in envelope.gates.blocked_reasons
    # Текст доступен оператору как черновик
    assert envelope.response.text == EMERGENCY_DRAFT_TEXT


@pytest.mark.asyncio
async def test_peripheral_scenarios_integration_contract(pg_session: AsyncSession):
    """Интеграционная проверка peripheral_setup и peripheral_diagnostics с политикой peripheral_clarify."""
    decision_service = ScenarioDecisionService(db=pg_session, ai_enabled=False)

    # 1. Заявка на настройку периферии без имени ПК
    task_without_pc = {
        "Id": 149001,
        "Name": "Не работает мышка и клавиатура",
        "Description": "Замените мышку, курсор не двигается на экране",
        "ServiceId": 71,
        "ServiceName": "Периферия",
    }
    envelope = await decision_service.analyze(task=task_without_pc)
    assert envelope.scenario_key in {"peripheral_setup", "peripheral_diagnostics"}
    assert envelope.outcome.outcome_key == "peripheral_clarify"
    assert envelope.policy.get("outcome_key") == "peripheral_clarify"
    assert (envelope.policy.get("status_id") or envelope.policy.get("target_status_id")) == 35
    assert envelope.response.state == "valid"
    assert "Здравствуйте! Для работы с периферийным устройством" in envelope.response.text
    assert "имя или инвентарный номер" in envelope.response.text
    assert envelope.gates.can_send_response is True

    # 2. Та же заявка с указанным валидным именем ПК
    task_with_pc = {
        "Id": 149002,
        "Name": "Не работает мышка NTEMW0123",
        "Description": "Курсор не двигается, компьютер NTEMW0123",
        "ServiceId": 71,
        "ServiceName": "Периферия",
    }
    envelope_with_pc = await decision_service.analyze(task=task_with_pc)
    assert envelope_with_pc.scenario_key in {"peripheral_setup", "peripheral_diagnostics"}
    assert envelope_with_pc.outcome.outcome_key == "in_work_standard"
    assert envelope_with_pc.policy.get("outcome_key") == "in_work_standard"
    assert envelope_with_pc.gates.can_send_response is True
