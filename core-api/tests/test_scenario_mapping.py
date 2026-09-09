"""
Тесты русскоязычного маппинга и отображения сценариев в доменном слое и API.
"""

from __future__ import annotations

import pytest

from shared.domain import (
    ActionProposed,
    DecisionEnvelope,
    DecisionResponse,
    Evidence,
    SCENARIO_DISPLAY_NAMES,
    SCENARIO_SHORT_NAMES,
    SUPPORTED_SCENARIO_KEYS,
    get_scenario_display_name,
)
from app.services.decision_envelope import envelope_to_legacy


def test_scenario_display_names_coverage():
    """Проверяем, что все ключевые сценарии имеют русскоязычные наименования."""
    expected_scenarios = [
        "install_printer",
        "printer_installation",
        "printer_hardware_service",
        "printer_scan_failure",
        "printer_print_failure",
        "create_user",
        "user_creation",
        "grant_wlan",
        "wlan_access",
        "redirect",
        "service_redirect",
        "offline_host",
        "file_lock",
        "physical_device",
        "hardware_repair",
        "rag_consultation",
        "duplicate_task",
        "duplicate",
        "consultation",
    ]
    for sc in expected_scenarios:
        assert sc in SCENARIO_DISPLAY_NAMES, f"Сценарий {sc} отсутствует в SCENARIO_DISPLAY_NAMES"
        assert sc in SCENARIO_SHORT_NAMES, f"Сценарий {sc} отсутствует в SCENARIO_SHORT_NAMES"
        assert sc in SUPPORTED_SCENARIO_KEYS, f"Сценарий {sc} отсутствует в SUPPORTED_SCENARIO_KEYS"
        assert get_scenario_display_name(sc) != sc, f"Для {sc} не определен человекочитаемый перевод"


def test_get_scenario_display_name_full_and_version():
    """Проверяем форматирование полного имени и версии."""
    assert get_scenario_display_name("install_printer") == "Установка принтера / МФУ"
    assert get_scenario_display_name("install_printer", version=1) == "Установка принтера / МФУ"
    assert get_scenario_display_name("install_printer", version=2) == "Установка принтера / МФУ (v2)"
    assert get_scenario_display_name("printer_hardware_service") == "Сервисный ремонт оргтехники"
    assert get_scenario_display_name("printer_scan_failure") == "Диагностика сетевого сканирования"
    assert get_scenario_display_name("printer_print_failure") == "Устранение сбоя очереди печати"
    assert get_scenario_display_name("create_user") == "Создание учётной записи (AD)"
    assert get_scenario_display_name("grant_wlan") == "Доступ к корпоративному Wi-Fi"
    assert get_scenario_display_name("redirect") == "Перенаправление в целевой сервис"


def test_get_scenario_display_name_short_for_badges():
    """Проверяем краткие имена для бейджей."""
    assert get_scenario_display_name("install_printer", short=True) == "Установка МФУ"
    assert get_scenario_display_name("printer_hardware_service", short=True) == "Ремонт МФУ"
    assert get_scenario_display_name("printer_scan_failure", short=True) == "Сбой сканирования"
    assert get_scenario_display_name("printer_print_failure", short=True) == "Сбой печати"
    assert get_scenario_display_name("create_user", short=True) == "Создание УЗ"
    assert get_scenario_display_name("offline_host", short=True) == "ПК офлайн"
    assert get_scenario_display_name("file_lock", short=True) == "Блокировка файла"
    assert get_scenario_display_name("physical_device", short=True) == "Каб. 112 (Ремонт)"
    assert get_scenario_display_name("rag_consultation", short=True) == "База знаний"
    assert get_scenario_display_name("duplicate_task", short=True) == "Дубликат"


def test_get_scenario_display_name_fallbacks():
    """Проверяем устойчивость к None, пустым строкам и неизвестным ключам."""
    assert get_scenario_display_name(None) == "Не определен"
    assert get_scenario_display_name("") == "Не определен"
    assert get_scenario_display_name(None, short=True) == "—"
    assert get_scenario_display_name("unknown_custom_rule") == "Unknown custom rule"
    assert get_scenario_display_name("unknown_custom_rule", short=True) == "unknown_custom_rule"


def test_decision_envelope_populates_scenario_title():
    """Проверяем, что DecisionEnvelope автоматически заполняет scenario_title."""
    outcome = ActionProposed(
        rule_key="scenario.install_printer",
        rule_version="2",
        outcome_key="install_printer_proposed",
        action="install_printer",
        parameters={"printer_ip": "10.0.0.1"},
        risk_level=1,
        requires_approval=True,
        evidence=[Evidence(source="rule", field="printer_address", code="valid")],
    )
    envelope = DecisionEnvelope(
        decision_id="test-dec-1",
        decision_version=1,
        scenario_key="install_printer",
        scenario_version=2,
        outcome=outcome,
        response=DecisionResponse(text="Устанавливаем МФУ", mode="template", state="valid"),
    )
    assert envelope.scenario_title == "Установка принтера / МФУ (v2)"
    assert envelope.response_draft == "Устанавливаем МФУ"

    legacy = envelope_to_legacy(envelope)
    assert legacy["scenario_title"] == "Установка принтера / МФУ (v2)"
    assert legacy["scenario_short_title"] == "Установка МФУ"
    assert legacy["name"] == "Установка принтера / МФУ (v2)"
