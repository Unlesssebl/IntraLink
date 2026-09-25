"""Unit tests for AutopilotReporter and DLP sanitization engine."""

import json
from unittest.mock import AsyncMock

import pytest

from core.diagnostic.ports import FastProbeResult
from core.intraservice.dto import ExtractedEntitiesDTO
from core.scenarios.reporter import (
    REDACTED_MARKER,
    AutopilotReporter,
    enforce_length_limit,
    human_scenario_title,
    sanitize_value,
)


def test_human_scenario_titles():
    """Verify human-readable scenario title resolution."""
    assert human_scenario_title("install_printer") == "Установка сетевого принтера"
    assert human_scenario_title("account_create") == "Создание учетной записи сотрудника"
    assert human_scenario_title("account_lock") == "Блокировка учетной записи"
    assert human_scenario_title("printer_spooler_restart") == "Перезапуск диспетчера печати Spooler"
    assert human_scenario_title("unknown_custom_action") == "Unknown custom action"
    assert human_scenario_title(None) == "Автоматический сценарий"


def test_dlp_sanitizer_masks_sensitive_keys_and_text():
    """Verify recursive sanitization masks passwords, tokens and secrets with ***REDACTED***."""
    payload = {
        "user": "ivanov.i",
        "password": "SuperSecretPassword123!",
        "it_password": "TempPassword456",
        "token": "secret_api_token_abc",
        "auth_b64": "YWRtaW46cGFzc3dvcmQ=",
        "nested": {
            "credentials": {
                "private_key": "-----BEGIN RSA PRIVATE KEY-----",
                "access_token": "ya29.a0AfH6SMA...",
            },
            "description": "Пользователь сообщил пароль: P@ssw0rd999 и Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        },
    }

    sanitized = sanitize_value(payload)

    assert sanitized["user"] == "ivanov.i"
    assert sanitized["password"] == REDACTED_MARKER
    assert sanitized["it_password"] == REDACTED_MARKER
    assert sanitized["token"] == REDACTED_MARKER
    assert sanitized["auth_b64"] == REDACTED_MARKER
    assert sanitized["nested"]["credentials"] == REDACTED_MARKER

    # Text pattern masking
    desc = sanitized["nested"]["description"]
    assert "P@ssw0rd999" not in desc
    assert REDACTED_MARKER in desc
    assert "eyJhbGciOi" not in desc


def test_format_start_comment_applied_and_technical():
    """Verify format_start_comment for applied and technical depths."""
    facts = ExtractedEntitiesDTO(
        pc_name="WKS-042",
        target_user="petrov.p",
        printer_address="10.20.30.40",
    )

    # Applied depth
    applied = AutopilotReporter.format_start_comment(
        task_id=101,
        scenario_key="install_printer",
        scenario_name="Установка сетевого принтера",
        depth="applied",
        facts=facts,
        confidence=0.92,
    )
    assert "[IntraLink Autopilot: Старт]" in applied
    assert "«Установка сетевого принтера»" in applied
    assert "ПК: `WKS-042`" in applied
    assert "Пользователь: `petrov.p`" in applied
    assert "Принтер: `10.20.30.40`" in applied

    # Technical depth
    technical = AutopilotReporter.format_start_comment(
        task_id=101,
        scenario_key="install_printer",
        depth="technical",
        facts=facts,
        confidence=0.92,
    )
    assert "[IntraLink Autopilot: Trace]" in technical
    assert "```json" in technical
    assert "```" in technical

    json_block = technical.split("```json\n")[1].split("\n```")[0]
    data = json.loads(json_block)
    assert data["event"] == "on_start"
    assert data["task_id"] == 101
    assert data["scenario_key"] == "install_printer"
    assert data["confidence"] == 0.92
    assert data["facts"]["pc_name"] == "WKS-042"


def test_format_diagnostic_comment_applied_and_technical():
    """Verify format_diagnostic_comment for FastSocketProbe socket checks."""
    probe = FastProbeResult(
        host="WKS-088",
        is_online=True,
        ports={5985: True, 445: True, 9100: False},
        rtt_ms=14.2,
    )

    # Applied depth
    applied = AutopilotReporter.format_diagnostic_comment(
        task_id=202,
        host="WKS-088",
        probe_result=probe,
        depth="applied",
    )
    assert "[IntraLink Autopilot: Диагностика сокетов]" in applied
    assert "Целевой хост `WKS-088`: В СЕТИ" in applied
    assert "отклик 14.2 мс" in applied
    assert "WinRM(5985): ДОСТУПЕН" in applied
    assert "SMB(445): ДОСТУПЕН" in applied
    assert "RAW(9100): ЗАКРЫТ" in applied

    # Technical depth
    tech = AutopilotReporter.format_diagnostic_comment(
        task_id=202,
        host="WKS-088",
        probe_result=probe,
        depth="technical",
    )
    assert "[IntraLink Autopilot: Trace]" in tech
    json_block = tech.split("```json\n")[1].split("\n```")[0]
    data = json.loads(json_block)
    assert data["event"] == "on_diagnostic"
    assert data["is_online"] is True
    assert data["rtt_ms"] == 14.2
    assert data["ports"]["5985"] is True
    assert data["ports"]["9100"] is False


def test_format_complete_comment_with_proof():
    """Verify format_complete_comment includes execution proof and sanitizes sensitive fields."""
    proof = {
        "winrm_exit_code": 0,
        "ldap_dn": "CN=Petrov Petr,OU=Users,DC=corp,DC=loc",
        "temp_password": "PlainPasswordToMask!",
        "stdout_tail": "Spooler service started successfully",
    }

    # Applied depth
    applied = AutopilotReporter.format_complete_comment(
        task_id=303,
        scenario_key="printer_spooler_restart",
        depth="applied",
        proof=proof,
        duration_seconds=3.4,
    )
    assert "[IntraLink Autopilot: Успех]" in applied
    assert "«Перезапуск диспетчера печати Spooler»" in applied
    assert "WinRM exit code: 0" in applied
    assert "CN=Petrov Petr" in applied
    assert "3.4с" in applied
    assert "PlainPasswordToMask!" not in applied

    # Technical depth
    tech = AutopilotReporter.format_complete_comment(
        task_id=303,
        scenario_key="printer_spooler_restart",
        depth="technical",
        proof=proof,
        duration_seconds=3.4,
    )
    json_block = tech.split("```json\n")[1].split("\n```")[0]
    data = json.loads(json_block)
    assert data["event"] == "on_complete"
    assert data["execution_proof"]["winrm_exit_code"] == 0
    assert data["execution_proof"]["temp_password"] == REDACTED_MARKER
    assert "PlainPasswordToMask!" not in tech


def test_format_pause_or_escalate_status_6_and_status_2():
    """Verify format_pause_or_escalate_comment distinguishes status 6 (pause) from status 2 (escalate)."""
    # Status 6: Pause / Waiting for user
    pause_applied = AutopilotReporter.format_pause_or_escalate_comment(
        task_id=404,
        scenario_key="install_printer",
        target_status_id=6,
        depth="applied",
        reason="Не указан IP-адрес сетевого принтера",
        missing_facts=["printer_address"],
        environment_barriers=["offline_host"],
        rounds=1,
    )
    assert "[IntraLink Autopilot: Приостановлена (Статус 6)]" in pause_applied
    assert "Отсутствуют реквизиты: `printer_address`" in pause_applied
    assert "Барьеры среды: offline_host" in pause_applied
    assert "Раунд диалога: 1" in pause_applied

    # Status 2: Escalation to human
    escalate_applied = AutopilotReporter.format_pause_or_escalate_comment(
        task_id=404,
        scenario_key="install_printer",
        target_status_id=2,
        depth="applied",
        reason="Превышен лимит раундов диалога с заявителем",
        missing_facts=["printer_address"],
        rounds=2,
    )
    assert "[IntraLink Autopilot: Эскалация инженеру (Статус 2)]" in escalate_applied
    assert "передан дежурному инженеру" in escalate_applied


def test_enforce_length_limit():
    """Verify long comments are truncated gracefully and code blocks remain closed."""
    short_text = "Короткий служебный комментарий"
    assert enforce_length_limit(short_text, max_length=100) == short_text

    long_dump = "A" * 4000
    truncated = enforce_length_limit(long_dump, max_length=1000)
    assert len(truncated) <= 1000
    assert "... [дамп усечен до лимита API]" in truncated

    # Truncation inside markdown code block
    code_dump = "```json\n" + ("x" * 4000) + "\n```"
    truncated_code = enforce_length_limit(code_dump, max_length=1000)
    assert truncated_code.endswith("```")
    assert "... [дамп усечен до лимита API]" in truncated_code


@pytest.mark.asyncio
async def test_post_audit_comment_fault_tolerance():
    """Verify post_audit_comment uses is_private=True and gracefully handles API network exceptions."""
    mock_client = AsyncMock()
    mock_client.add_task_comment.return_value = True

    reporter = AutopilotReporter()

    # Success case
    ok = await reporter.post_audit_comment(
        client=mock_client,
        task_id=777,
        comment="Служебная заметка",
        auth_b64="token",
    )
    assert ok is True
    mock_client.add_task_comment.assert_awaited_once_with(
        task_id=777,
        comment="Служебная заметка",
        is_private=True,
        auth_b64="token",
    )

    # Failure case (Fault Tolerance)
    mock_client.add_task_comment.side_effect = TimeoutError("IntraService API timed out")
    res = await reporter.post_audit_comment(
        client=mock_client,
        task_id=777,
        comment="Служебная заметка",
    )
    assert res is False  # Does not raise exception!
