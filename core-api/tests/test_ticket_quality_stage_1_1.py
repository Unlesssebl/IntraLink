"""Тесты соответствия требованиям Раздела 3 Технического плана Этапа 1.1:
факты, нормализация, диагностика, маршрутизация, согласованность и совместимость.
"""
from __future__ import annotations

import socket
import pytest

from shared.domain import (
    CandidateOutcome,
    DecisionEnvelope,
    DecisionRoutingInfo,
    Evidence,
    FactBag,
    FactObservation,
    FactSource,
    FactState,
    ResolutionProposed,
)
from shared.normalizer import (
    normalize_pc_name,
    normalize_printer_address,
    is_valid_pc_name,
    is_valid_printer_name,
    extract_pc_names_from_text,
    extract_printer_addresses_from_text,
)
from app.services.facts.collectors import collect_deterministic
from app.services.facts.merger import merge_observations
from app.services.rules.offline_host import OfflineHostRule
from app.services.rules.physical_device import PhysicalDeliveryRule
from app.services.rules.redirect import ServiceRedirectRule, classify_target_service
from app.services.scenarios import ScenarioContext, ScenarioRegistry
from app.services.scenarios.registry import RouteResult
from app.services.decision_compiler import DecisionCompiler
from scripts.ticket_quality_baseline import prevent_network, BlockedNetworkCallError


# ==============================================================================
# 1. Группа «Устройство / намерение»
# ==============================================================================

@pytest.mark.asyncio
async def test_audio_device_detected_and_not_classified_as_printer():
    """Колонки/наушники классифицируются как audio, printer_name не создается."""
    task = {
        "Id": 140408,
        "Name": "Подключить колонки",
        "Description": "Прошу подключить колонки к компьютеру NTEMW0123",
        "ServiceId": 19,
    }
    observations = await collect_deterministic(task)
    facts = merge_observations(observations)

    assert facts.valid_value("device_type") == "audio"
    assert facts.valid_value("pc_name") == "NTEMW0123"
    assert facts.valid_value("printer_name") is None

    registry = ScenarioRegistry()
    ctx = ScenarioContext(task=task, facts=facts)
    route_res = registry.route_result(ctx)

    assert route_res.scenario.definition.key == "peripheral_setup"
    assert route_res.scenario.definition.key != "install_printer"


@pytest.mark.asyncio
async def test_negative_headphone_intent_routes_to_peripheral_diagnostics():
    """«Не подключаются наушники» -> периферийная диагностика, а не установка."""
    task = {
        "Id": 140511,
        "Name": "Наушники не подключаются",
        "Description": "В разъеме нет звука, наушники не работают на компьютере NTEMW0555",
        "ServiceId": 19,
    }
    observations = await collect_deterministic(task)
    facts = merge_observations(observations)

    assert facts.valid_value("device_type") == "audio"
    assert facts.valid_value("printer_name") is None

    registry = ScenarioRegistry()
    ctx = ScenarioContext(task=task, facts=facts)
    route_res = registry.route_result(ctx)

    assert route_res.scenario.definition.key == "peripheral_diagnostics"
    assert route_res.scenario.definition.key != "install_printer"


@pytest.mark.asyncio
async def test_printer_negative_intent_does_not_route_to_install_printer():
    """«Принтер не подключается» или «Принтер не печатает» не маршрутизируется в install_printer."""
    task = {
        "Id": 140999,
        "Name": "Принтер не подключается",
        "Description": "HP LaserJet перестал определяться на ПК NTEMW0123",
        "ServiceId": 19,
    }
    observations = await collect_deterministic(task)
    facts = merge_observations(observations)

    registry = ScenarioRegistry()
    ctx = ScenarioContext(task=task, facts=facts)
    route_res = registry.route_result(ctx)

    assert route_res.scenario.definition.key != "install_printer"


@pytest.mark.asyncio
async def test_printer_reinstall_intent_routes_to_install_printer():
    """Неисправность с явным запросом на переустановку принтера -> install_printer."""
    task = {
        "Id": 140998,
        "Name": "Переустановить принтер",
        "Description": "Слетел драйвер, требуется переустановка принтера HP LaserJet на NTEMW0123",
        "ServiceId": 19,
    }
    observations = await collect_deterministic(task)
    facts = merge_observations(observations)

    registry = ScenarioRegistry()
    ctx = ScenarioContext(task=task, facts=facts)
    route_res = registry.route_result(ctx)

    assert route_res.scenario.definition.key == "install_printer"


# ==============================================================================
# 2. Группа «Имена и адреса»
# ==============================================================================

def test_normalizer_pc_name_formats():
    """Нормализация пробелов, дефисов, кириллического префикса и регистра."""
    assert normalize_pc_name("NTEMW 0771") == "NTEMW0771"
    assert normalize_pc_name("NTEMW-0771") == "NTEMW0771"
    assert normalize_pc_name("ntemw0771") == "NTEMW0771"
    # Кириллица: Н, Т, Е, М, W
    assert normalize_pc_name("НТЕМW0771") == "NTEMW0771"
    # Смешанный префикс ТКТ
    assert normalize_pc_name("ТКТ 0151") == "TKT0151"
    assert normalize_pc_name("tkt-0151") == "TKT0151"


def test_normalizer_rejects_bare_numbers_and_versions():
    """Голый номер и версии ПО не становятся валидными именами ПК."""
    assert normalize_pc_name("0771") is None
    assert normalize_pc_name("8.3") is None
    assert normalize_pc_name("1С:Предприятие") is None
    assert is_valid_pc_name("0771") is False


@pytest.mark.asyncio
async def test_multiple_pcs_in_text_creates_ambiguity():
    """Несколько разных ПК в тексте создают ambiguity и не выбирают первый слепо."""
    task = {
        "Id": 140244,
        "Name": "Настройка оборудования",
        "Description": "Нужно проверить компьютеры NTEMW0111 и NTEMW0222",
        "ServiceId": 19,
    }
    observations = await collect_deterministic(task)
    facts = merge_observations(observations)

    assert facts.facts["pc_name"].state is FactState.AMBIGUOUS


def test_printer_address_normalization():
    """Нормализация сетевого адреса принтера исправляет опечатки IP и DNS-имен."""
    assert normalize_printer_address("10,244 1.20") == "10.244.1.20"
    assert normalize_printer_address("SCSP 0001") == "scsp0001"
    addresses = extract_printer_addresses_from_text("Подключен МФУ SCSP 0001 с адресом 10.244.1.20")
    assert "10.244.1.20" in addresses
    assert "scsp0001" in addresses


# ==============================================================================
# 3. Группа «История и диагностика»
# ==============================================================================

def test_offline_host_rule_conditions():
    """OfflineHostRule срабатывает только при достоверно завершенной проверке offline."""
    rule = OfflineHostRule()
    task = {"Id": 100, "Name": "Не открывается база", "Description": "Ошибка"}

    # 1. Корректный отрицательный результат
    diag_offline = {"is_online": False, "status": "offline", "target": "NTEMW0123"}
    dec = rule.evaluate(task, diag=diag_offline)
    assert dec is not None
    assert dec.status_id == 35
    assert dec.template_key == "pc_offline"
    assert "не удалось связаться с пк" in dec.comment.lower()

    # 2. Ошибка средства диагностики -> НЕ offline
    diag_error = {"is_online": False, "status": "error", "target": "NTEMW0123"}
    assert rule.evaluate(task, diag=diag_error) is None

    # 3. Неизвестный статус -> НЕ offline
    diag_unknown = {"is_online": False, "status": "unknown", "target": "NTEMW0123"}
    assert rule.evaluate(task, diag=diag_unknown) is None

    # 4. Отсутствует is_online
    assert rule.evaluate(task, diag={"target": "NTEMW0123"}) is None
    assert rule.evaluate(task, diag=None) is None


def test_physical_delivery_rule_negative_delivery_intent():
    """«Принесу завтра» не означает, что устройство уже доставлено."""
    rule = PhysicalDeliveryRule()
    task = {
        "Id": 133329,
        "Name": "Не включается ПК",
        "Description": "Принесу завтра в 112 кабинет",
        "ServiceId": 32,
    }
    # Нет комментария о фактическом приеме в 112 кабинете
    decision = rule.evaluate(task=task, diag={"is_online": False, "target": "NTEMW0123"})
    assert decision is not None
    # Должно оставаться 48 (Ожидание устройства), а не 27 (В работе)
    assert decision.status_id == 48
    assert decision.template_key == "hardware_repair"


# ==============================================================================
# 4. Группа «Маршрутизация и редиректы»
# ==============================================================================

def test_general_pc_lag_with_1c_mention_not_redirected():
    """Общие тормоза с упоминанием 1С остаются на 1-й линии (pc_performance)."""
    text = "Тормозит компьютер NTEMW0123, зависает рабочий стол и 1С"
    target_root, reason = classify_target_service(text, current_service_id=19)
    # Не классифицировать как 1С (06); относится к разделу 1-й линии (03)
    assert target_root != "06"

    rule = ServiceRedirectRule()
    task = {
        "Id": 139762,
        "ServiceId": 19,
        "Name": "Тормозит ПК",
        "Description": text,
    }
    decision = rule.evaluate(task)
    assert decision is None  # Запрещен ложный редирект


def test_os_reinstallation_with_1c_mention_not_redirected():
    """Переустановка ОС после сбоя 1С остается на 1-й линии (os_reinstallation)."""
    text = "Нужна переустановка Windows на ПК NTEMW0444, после ошибки 1С система не стартует"
    target_root, reason = classify_target_service(text, current_service_id=19)
    # Не классифицировать как 1С (06); относится к разделу 1-й линии (03)
    assert target_root != "06"

    rule = ServiceRedirectRule()
    task = {
        "Id": 140686,
        "ServiceId": 19,
        "Name": "Переустановка Windows",
        "Description": text,
    }
    assert rule.evaluate(task) is None


def test_pure_1c_error_still_redirected():
    """Чистая ошибка 1С из общих вопросов перенаправляется в 06."""
    text = "Ошибка при проведении авансового отчета в 1С:Бухгалтерия"
    target_root, reason = classify_target_service(text, current_service_id=16)
    assert target_root == "06"

    rule = ServiceRedirectRule()
    task = {"Id": 140997, "ServiceId": 16, "Name": "Ошибка 1С", "Description": text}
    dec = rule.evaluate(task)
    assert dec is not None
    assert dec.is_redirect is True
    assert dec.status_id == 30


def test_crypto_pro_certificate_request():
    """Запрос на установку сертификата ЭЦП КриптоПро классифицируется в 09."""
    text = "Установить сертификат ЭЦП КриптоПро на рабочее место"
    target_root, reason = classify_target_service(text, current_service_id=16)
    assert target_root == "09"


def test_route_result_ambiguity_calculation():
    """Если два сценария имеют близкий score, выставляется флаг is_ambiguous."""
    registry = ScenarioRegistry()
    scenario = registry.get("consultation")
    assert scenario is not None

    res = RouteResult(
        scenario=scenario,
        score=0.88,
        runner_up_score=0.85,  # Разница 0.03 < margin 0.10
        is_ambiguous=True,
        reasons=["Close match"],
    )
    assert res.is_ambiguous is True
    assert res.score == 0.88
    assert res.runner_up_score == 0.85


# ==============================================================================
# 5. Группа «Согласованность решения и компилятор»
# ==============================================================================

@pytest.mark.asyncio
async def test_decision_compiler_emits_routing_and_clarifications():
    """Компилятор включает routing и structured clarifications в DecisionEnvelope."""
    from shared.domain import ClarificationRequired

    outcome = ClarificationRequired(
        rule_key="peripheral_clarify",
        rule_version="1",
        outcome_key="peripheral_clarify_pc",
        missing_fields=["pc_name"],
        evidence=[Evidence(source="rule", field="pc_name", code="missing")],
    )
    routing_info = DecisionRoutingInfo(
        selected_score=0.95,
        runner_up_score=0.50,
        is_ambiguous=False,
        reasons=["Explicit diagnostic match"],
    )
    candidate = CandidateOutcome(
        candidate_id="rule:1",
        source="rule",
        outcome=outcome,
        evidence_refs=["rule:ref"],
        score=0.95,
        routing=routing_info,
    )

    compiler = DecisionCompiler(adjudicator=None)
    envelope = await compiler.compile(
        scenario_key="peripheral_diagnostics",
        scenario_version=1,
        scenario_risk=0,
        allowed_actions=set(),
        facts=FactBag(),
        candidates=[candidate],
    )

    assert envelope.routing is not None
    assert envelope.routing.selected_score == 0.95
    assert envelope.routing.is_ambiguous is False
    assert len(envelope.clarifications) >= 1
    assert envelope.clarifications[0]["field"] == "pc_name"
    assert envelope.clarifications[0]["reason"] == "missing"


# ==============================================================================
# 6. Группа «Совместимость и сетевая изоляция Replay»
# ==============================================================================

def test_prevent_network_blocks_all_socket_connections():
    """Любая попытка внешнего вызова через сокет немедленно прерывается."""
    with prevent_network():
        with pytest.raises(BlockedNetworkCallError):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("8.8.8.8", 53))


def test_decision_envelope_backward_compatibility():
    """Старые снимки DecisionEnvelope без полей routing и clarifications парсятся без ошибок."""
    old_envelope_data = {
        "decision_id": "test-uuid",
        "scenario_key": "consultation",
        "scenario_version": 1,
        "facts_summary": {},
        "outcome": {
            "kind": "resolution",
            "rule_key": "std",
            "rule_version": "1",
            "outcome_key": "std",
            "target_status_id": 27,
        },
        "gates": {
            "can_execute_action": False,
            "requires_approval": False,
            "blocked_reasons": [],
        },
    }

    envelope = DecisionEnvelope.model_validate(old_envelope_data)
    assert envelope.routing is None
    assert envelope.clarifications == []
    assert envelope.scenario_key == "consultation"
