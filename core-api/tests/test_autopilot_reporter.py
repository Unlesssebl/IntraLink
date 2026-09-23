"""Unit tests for AutopilotReporter and DLP sanitization engine."""

import json
import pytest

from app.services.autopilot_reporter import (
    AutopilotReporter,
    MAX_COMMENT_LENGTH,
    REDACTED_MARKER,
    human_scenario_title,
    sanitize_value,
    enforce_length_limit,
)


def test_human_scenario_title():
    assert human_scenario_title("install_printer") == "Установка сетевого принтера"
    assert human_scenario_title("directum_access") == "Выдача доступа DIRECTUM"
    assert human_scenario_title("cancel_duplicate") == "Отмена дубликата заявки"
    assert human_scenario_title("custom_unknown_task") == "Custom unknown task"
    assert human_scenario_title(None) == "Автоматический сценарий"


def test_sanitize_value_masks_sensitive_dictionary_keys():
    raw_facts = {
        "host": "NTEMW0047",
        "ip": "10.244.12.105",
        "printer": {
            "model": "Kyocera M2040dn",
            "port": "10.244.12.45",
            "admin_password": "SuperSecretPassword!123",
            "service_token": "secret_token_value_abc",
        },
        "service_meta": {
            "auth_b64": "dXNlcjpwYXNz",
            "api_key": "k-1234567890",
            "private_key": "-----BEGIN PRIVATE KEY-----",
        },
        "credentials": {
            "username": "admin",
            "pass": "123456",
        },
        "safe_list": [
            {"name": "worker1", "password": "WorkerSecretPassword"},
            {"name": "worker2", "status": "ok"},
        ],
    }

    sanitized = sanitize_value(raw_facts)

    assert sanitized["host"] == "NTEMW0047"
    assert sanitized["ip"] == "10.244.12.105"
    assert sanitized["printer"]["model"] == "Kyocera M2040dn"
    assert sanitized["printer"]["port"] == "10.244.12.45"
    # Секретные ключи должны быть замаскированы
    assert sanitized["printer"]["admin_password"] == REDACTED_MARKER
    assert sanitized["printer"]["service_token"] == REDACTED_MARKER
    assert sanitized["service_meta"]["auth_b64"] == REDACTED_MARKER
    assert sanitized["service_meta"]["api_key"] == REDACTED_MARKER
    assert sanitized["service_meta"]["private_key"] == REDACTED_MARKER
    # Опасный контейнер верхнего уровня целиком замаскирован
    assert sanitized["credentials"] == REDACTED_MARKER
    assert sanitized["safe_list"][0]["password"] == REDACTED_MARKER
    assert sanitized["safe_list"][0]["name"] == "worker1"
    assert sanitized["safe_list"][1]["status"] == "ok"


def test_sanitize_value_masks_inline_text_patterns():
    text_with_secrets = "Пользователь указал пароль: MySecretPass2026! при создании тикета. Токен=xyz_token_999."
    bearer_text = "Используйте заголовок Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDN для доступа."

    sanitized_text = sanitize_value(text_with_secrets)
    assert "MySecretPass2026!" not in sanitized_text
    assert REDACTED_MARKER in sanitized_text
    assert "xyz_token_999" not in sanitized_text

    sanitized_bearer = sanitize_value(bearer_text)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDN" not in sanitized_bearer
    assert REDACTED_MARKER in sanitized_bearer


def test_format_start_comment_applied():
    comment = AutopilotReporter.format_start_comment(
        run_id="run-123",
        task_id=105420,
        scenario_key="install_printer",
        scenario_version=2,
        depth="applied",
        target_host={"name": "NTEMW0047", "ip": "10.244.12.105", "rtt_ms": 3.8},
        confidence=0.97,
        facts={"printer_model": "HP LaserJet M428"},
    )

    assert "[IntraLink Autopilot: Старт]" in comment
    assert "Установка сетевого принтера" in comment
    assert "`NTEMW0047`" in comment
    assert "10.244.12.105" in comment
    assert "3.8 мс" in comment
    assert "```json" not in comment


def test_format_start_comment_technical():
    comment = AutopilotReporter.format_start_comment(
        run_id="run-123",
        task_id=105420,
        scenario_key="install_printer",
        scenario_version=2,
        depth="technical",
        target_host={"name": "NTEMW0047", "ip": "10.244.12.105"},
        confidence=0.97,
        facts={"printer_model": "HP LaserJet M428", "admin_password": "PlainPassword!"},
    )

    assert "[IntraLink Autopilot: Trace]" in comment
    assert "Событие: on_start" in comment
    assert "install_printer" in comment
    assert "```json" in comment

    # Проверяем, что внутри находится валидный парсируемый JSON
    json_str = comment.split("```json\n")[1].split("\n```")[0]
    data = json.loads(json_str)

    assert data["event"] == "on_start"
    assert data["run_id"] == "run-123"
    assert data["task_id"] == 105420
    assert data["scenario_key"] == "install_printer"
    assert data["confidence"] == 0.97
    assert data["target_host"]["name"] == "NTEMW0047"
    assert data["facts"]["printer_model"] == "HP LaserJet M428"
    assert data["facts"]["admin_password"] == REDACTED_MARKER
    assert "PlainPassword!" not in comment


def test_format_pause_comment_applied_and_technical():
    # Applied
    comment_applied = AutopilotReporter.format_pause_comment(
        run_id="run-456",
        task_id=105421,
        scenario_key="install_printer",
        depth="applied",
        reason="Не удалось определить модель принтера",
        missing_facts=["printer_model", "target_ip"],
    )
    assert "[IntraLink Autopilot: Пауза]" in comment_applied
    assert "Не удалось определить модель принтера" in comment_applied
    assert "`printer_model`" in comment_applied
    assert "`target_ip`" in comment_applied
    assert "```json" not in comment_applied

    # Technical
    comment_tech = AutopilotReporter.format_pause_comment(
        run_id="run-456",
        task_id=105421,
        scenario_key="install_printer",
        depth="technical",
        reason="Не удалось определить модель принтера",
        missing_facts=["printer_model"],
        error_detail={"code": "ERR_FACTS_MISSING", "auth_token": "secret_123"},
    )
    assert "[IntraLink Autopilot: Trace]" in comment_tech
    assert "on_pause_or_error" in comment_tech

    json_str = comment_tech.split("```json\n")[1].split("\n```")[0]
    data = json.loads(json_str)
    assert data["event"] == "on_pause_or_error"
    assert data["missing_facts"] == ["printer_model"]
    assert data["error_detail"]["code"] == "ERR_FACTS_MISSING"
    assert data["error_detail"]["auth_token"] == REDACTED_MARKER


def test_format_complete_comment_applied_and_technical():
    # Applied
    comment_applied = AutopilotReporter.format_complete_comment(
        run_id="run-789",
        task_id=105422,
        scenario_key="install_printer",
        depth="applied",
        outcome="Принтер Kyocera M2040dn успешно установлен и протестирован.",
        duration_seconds=15.42,
    )
    assert "[IntraLink Autopilot: Успех]" in comment_applied
    assert "Установка сетевого принтера" in comment_applied
    assert "Kyocera M2040dn успешно установлен" in comment_applied
    assert "15.4 сек" in comment_applied

    # Technical
    comment_tech = AutopilotReporter.format_complete_comment(
        run_id="run-789",
        task_id=105422,
        scenario_key="install_printer",
        depth="technical",
        outcome="OK",
        proof={"job_id": "job-1", "exit_code": 0, "session_token": "token_abc"},
        duration_seconds=15.42,
    )
    assert "[IntraLink Autopilot: Trace]" in comment_tech
    assert "on_complete" in comment_tech

    json_str = comment_tech.split("```json\n")[1].split("\n```")[0]
    data = json.loads(json_str)
    assert data["event"] == "on_complete"
    assert data["duration_seconds"] == 15.42
    assert data["execution_proof"]["exit_code"] == 0
    assert data["execution_proof"]["session_token"] == REDACTED_MARKER


def test_enforce_length_limit_with_large_payload():
    huge_facts = {f"key_{i}": "x" * 200 for i in range(50)}
    comment = AutopilotReporter.format_start_comment(
        run_id="run-huge",
        task_id=999999,
        scenario_key="install_printer",
        depth="technical",
        facts=huge_facts,
    )

    assert len(comment) <= MAX_COMMENT_LENGTH
    assert "... [дамп усечен до лимита API]" in comment
    assert comment.endswith("```")


def test_reporter_handles_none_and_empty_values_safely():
    # Проверяем старт без хоста и фактов
    start = AutopilotReporter.format_start_comment(
        task_id=111,
        scenario_key="unknown_scenario",
        depth="applied",
        target_host=None,
    )
    assert "[IntraLink Autopilot: Старт]" in start

    # Проверяем паузу без параметров
    pause = AutopilotReporter.format_pause_comment(
        task_id=111,
        scenario_key="unknown_scenario",
        depth="applied",
        reason=None,
        missing_facts=None,
    )
    assert "[IntraLink Autopilot: Пауза]" in pause

    # Проверяем завершение без outcome и duration
    complete = AutopilotReporter.format_complete_comment(
        task_id=111,
        scenario_key="unknown_scenario",
        depth="applied",
        outcome=None,
        duration_seconds=None,
    )
    assert "[IntraLink Autopilot: Успех]" in complete
