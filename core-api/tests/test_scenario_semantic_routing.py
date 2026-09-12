"""
Тесты семантического подбора сценариев Pass 1 и целостности режимов:
off, shadow, canary, on.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.scenarios.base import ScenarioContext
from app.services.scenarios.intent_matcher import (
    ScenarioIntentMatcher,
    SemanticCandidate,
    ensure_matcher_initialized,
)
from app.services.scenarios.registry import (
    ScenarioRegistry,
    get_scenario_registry,
    RouteResult,
)
from app.services.scenario_decision import ScenarioDecisionService
from shared.domain import FactBag


@pytest.mark.asyncio
async def test_intent_matcher_direct_ranking():
    """Проверка прямого сопоставления ключевых интентов с прототипами."""
    # 1. Сбой ПК / производительность
    ctx_pc = ScenarioContext(
        task={
            "Id": 101,
            "Name": "Компьютер зависает и очень медленно работает",
            "Description": "Постоянная загрузка процессора, программы висят",
        },
        facts=FactBag(),
    )
    cand_pc = await ScenarioIntentMatcher.match(ctx_pc)
    assert cand_pc is not None
    assert cand_pc.scenario_key == "pc_performance"
    assert cand_pc.score > 0.45

    # 2. Сбой печати
    ctx_printer = ScenarioContext(
        task={
            "Id": 102,
            "Name": "Принтер не печатает документы",
            "Description": "Документ завис в очереди печати принтера, печать не идет",
        },
        facts=FactBag(),
    )
    cand_printer = await ScenarioIntentMatcher.match(ctx_printer)
    assert cand_printer is not None
    assert cand_printer.scenario_key == "printer_print_failure"

    # 3. Периферия / мышь и клавиатура
    ctx_periph = ScenarioContext(
        task={
            "Id": 103,
            "Name": "Не работает мышь и клавиатура",
            "Description": "Курсор замер на экране, клавиатура не реагирует на нажатия",
        },
        facts=FactBag(),
    )
    cand_periph = await ScenarioIntentMatcher.match(ctx_periph)
    assert cand_periph is not None
    assert cand_periph.scenario_key == "peripheral_diagnostics"


@pytest.mark.asyncio
async def test_intent_matcher_negation_rules():
    """Проверка инварианта 5: маркеры отрицания исключают ошибочные действия."""
    # Запрос: "Не надо устанавливать принтер, принтер не печатает"
    # Должен исключить peripheral_setup и install_printer
    ctx = ScenarioContext(
        task={
            "Id": 201,
            "Name": "Не нужно устанавливать принтер",
            "Description": "Печать не требуется устанавливать заново, принтер не печатает документы",
        },
        facts=FactBag(),
    )
    cand = await ScenarioIntentMatcher.match(ctx)
    assert cand is not None
    assert cand.scenario_key not in ("peripheral_setup", "install_printer")
    assert cand.scenario_key == "printer_print_failure"


@pytest.mark.asyncio
async def test_intent_matcher_app_specific_slowdown():
    """Проверка исключения pc_performance при локальных сбоях одного приложения (1C)."""
    ctx = ScenarioContext(
        task={
            "Id": 202,
            "Name": "Зависает программа",
            "Description": "Тормозит только 1С при формировании отчета",
        },
        facts=FactBag(),
    )
    cand = await ScenarioIntentMatcher.match(ctx)
    if cand:
        assert cand.scenario_key != "pc_performance"


@pytest.mark.asyncio
async def test_intent_matcher_comments_priority():
    """Проверка инварианта 6: учет последнего публичного комментария заявителя."""
    ctx = ScenarioContext(
        task={
            "Id": 203,
            "Name": "Странный шум в системном блоке",
            "Description": "Гудит вентилятор",
        },
        facts=FactBag(),
        comments=[
            {"Comment": "Внутренний комментарий", "IsPrivate": True},
            {"Comment": "Мышь и клавиатура полностью отключились и не работают", "IsPrivate": False},
        ],
    )
    cand = await ScenarioIntentMatcher.match(ctx)
    assert cand is not None
    assert cand.scenario_key == "peripheral_diagnostics"


@pytest.mark.asyncio
async def test_intent_matcher_off_mode():
    """В режиме off матчер не производит вычислений и возвращает None."""
    with patch.object(settings, "SCENARIO_SEMANTIC_ROUTING_MODE", "off"):
        ctx = ScenarioContext(
            task={"Id": 301, "Name": "Компьютер зависает", "Description": "Тормозит ПК"},
            facts=FactBag(),
        )
        cand = await ScenarioIntentMatcher.match(ctx)
        assert cand is None


def test_canary_hash_distribution():
    """Проверка детерминированного хеш-бакетинга для режима canary."""
    # 0% - никогда
    assert not ScenarioRegistry.canary_selected(100, 0)
    assert not ScenarioRegistry.canary_selected(999, 0)

    # 100% - всегда
    assert ScenarioRegistry.canary_selected(100, 100)
    assert ScenarioRegistry.canary_selected(999, 100)

    # Детерминированность: один и тот же task_id всегда дает одинаковый результат
    res1 = ScenarioRegistry.canary_selected(4242, 30)
    res2 = ScenarioRegistry.canary_selected(4242, 30)
    assert res1 == res2


def test_pinned_scenario_priority_over_semantic():
    """Закрепленный сценарий имеет абсолютный приоритет над семантическим подбором."""
    registry = get_scenario_registry()
    ctx = ScenarioContext(
        task={"Id": 401, "Name": "Компьютер зависает", "Description": "Тормозит ПК"},
        facts=FactBag(),
    )
    sem_candidate = SemanticCandidate(
        scenario_key="pc_performance",
        score=0.95,
        runner_up_key=None,
        runner_up_score=0.0,
        margin=0.95,
        is_ambiguous=False,
    )
    # Закрепляем install_printer
    res = registry.route_result(
        ctx,
        pinned_key="install_printer",
        pinned_version=1,
        semantic_candidate=sem_candidate,
    )
    assert res.scenario.definition.key == "install_printer"
    assert "pinned_scenario:install_printer:v1" in res.reasons[0]


def test_shadow_mode_preserves_deterministic_pass0():
    """В режиме shadow семантический кандидат логируется, но не меняет детерминированный выбор."""
    registry = get_scenario_registry()
    ctx = ScenarioContext(
        task={"Id": 501, "Name": "Заявка на консультацию", "Description": "Общий вопрос"},
        facts=FactBag(),
    )
    sem_candidate = SemanticCandidate(
        scenario_key="pc_performance",
        score=0.92,
        runner_up_key="printer_print_failure",
        runner_up_score=0.20,
        margin=0.72,
        is_ambiguous=False,
    )

    with patch.object(settings, "SCENARIO_SEMANTIC_ROUTING_MODE", "shadow"):
        res = registry.route_result(ctx, semantic_candidate=sem_candidate)
        # Так как это консультация и нет доменного совпадения Pass 0, в shadow режиме сценарий остается consultation/rag
        assert res.scenario.definition.key == "consultation"
        assert res.semantic_divergence is True
        assert res.semantic_candidate.scenario_key == "pc_performance"


def test_on_mode_promotes_semantic_candidate():
    """В режиме on валидный семантический кандидат подбирает сценарий при отсутствии доменного Pass 0."""
    registry = get_scenario_registry()
    ctx = ScenarioContext(
        task={"Id": 601, "Name": "Общий вопрос", "Description": "Непонятный запрос"},
        facts=FactBag(),
    )
    sem_candidate = SemanticCandidate(
        scenario_key="pc_performance",
        score=0.88,
        runner_up_key="consultation",
        runner_up_score=0.50,
        margin=0.38,
        is_ambiguous=False,
    )

    with patch.object(settings, "SCENARIO_SEMANTIC_ROUTING_MODE", "on"):
        with patch.object(settings, "SCENARIO_SEMANTIC_MIN_SCORE", 0.82):
            with patch.object(settings, "SCENARIO_SEMANTIC_MIN_MARGIN", 0.08):
                res = registry.route_result(ctx, semantic_candidate=sem_candidate)
                assert res.scenario.definition.key == "pc_performance"
                assert "semantic_pass1_selected" in res.reasons


@pytest.mark.asyncio
async def test_scenario_decision_service_records_semantic_telemetry():
    """Проверка записи полей семантической маршрутизации в CandidateOutcome.routing."""
    mock_db = AsyncMock(spec=AsyncSession)
    mock_resolver = AsyncMock()
    mock_resolver.resolve.return_value = {
        "status_id": 35,
        "comment": "В работе",
        "requires_approval": False,
    }
    service = ScenarioDecisionService(
        mock_db,
        policy_resolver=mock_resolver,
        ai_enabled=False,
    )

    task = {
        "Id": 701,
        "Name": "Компьютер зависает и очень медленно работает",
        "Description": "Постоянная загрузка процессора, программы висят",
        "service_id": 99,
    }

    envelope = await service.analyze(task=task)
    assert envelope is not None
    # Проверяем, что routing telemetry корректно заполнена
    for cand in envelope.candidates:
        if cand.routing:
            assert cand.routing.semantic_mode is not None
            assert cand.routing.semantic_candidate_key is not None
