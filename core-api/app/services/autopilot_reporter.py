"""Autopilot internal reporter and DLP sanitization engine.

Formats non-intrusive internal service comments (IsPrivateComment = true)
for IntraService tickets across three key lifecycle events:
- on_start: ticket claimed by autopilot
- on_pause_or_error: execution paused, facts missing, or worker failed
- on_complete: scenario successfully executed and verified

Supports two depth levels:
- applied: 1-2 concise, clear Russian sentences for helpdesk engineers
- technical: structured, sanitized JSON trace dump for administrators/auditors
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Mapping

UTC = dt.timezone.utc

MAX_COMMENT_LENGTH = 3500
REDACTED_MARKER = "***REDACTED***"

SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|authorization|credential|api_key|auth_b64|private_key|access_token|refresh_token)",
    re.IGNORECASE,
)

SECRET_TEXT_PATTERNS = [
    re.compile(r"(?i)\b(password|пароль|secret|токен|token|api_key|auth_b64)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)\bBearer\s+([a-zA-Z0-9_\-\.]{15,})\b"),
    re.compile(r"(?i)\bBasic\s+([a-zA-Z0-9+/=]{15,})\b"),
]

SCENARIO_TITLES: dict[str, str] = {
    "install_printer": "Установка сетевого принтера",
    "printer_install": "Установка сетевого принтера",
    "service_09_directum": "Выдача доступа DIRECTUM",
    "directum_access": "Выдача доступа DIRECTUM",
    "sbis_setup": "Настройка СБИС / ЭДО",
    "edo_sbis": "Настройка СБИС / ЭДО",
    "etran_setup": "Настройка АС ЭТРАН",
    "cancel_duplicate": "Отмена дубликата заявки",
    "redirect_service": "Перенаправление заявки в корректный сервис",
}


def human_scenario_title(scenario_key: str | None) -> str:
    """Return human-readable Russian scenario title."""
    if not scenario_key:
        return "Автоматический сценарий"
    key = str(scenario_key).strip().lower()
    return SCENARIO_TITLES.get(key, key.replace("_", " ").capitalize())


def resolve_internal_comments_config(
    global_setting: Any,
    scenario: Any | None = None,
) -> tuple[bool, str]:
    """Resolve comments enabled flag and depth cascading from scenario to global settings."""
    scenario_cfg = (getattr(scenario, "config_json", None) or {}) if scenario else {}
    enabled = scenario_cfg.get("internal_comments_enabled")
    if enabled is None:
        enabled = getattr(global_setting, "internal_comments_enabled", True)

    depth = scenario_cfg.get("internal_comments_depth")
    if depth is None:
        depth = getattr(global_setting, "internal_comments_depth", "applied")

    clean_depth = str(depth).strip().lower()
    if clean_depth not in {"applied", "technical"}:
        clean_depth = "applied"

    return bool(enabled), clean_depth


def is_internal_comments_allowed(
    *,
    rollout_mode: str | None,
    task_id: int,
    canary_percent: int = 10,
) -> bool:
    """Return True if internal comments are allowed under the given rollout mode."""
    mode = (rollout_mode or "active").strip().lower()
    if mode == "shadow":
        return False
    if mode == "canary":
        try:
            from app.services.scenarios import get_scenario_registry
            return get_scenario_registry().canary_selected(task_id, canary_percent)
        except Exception:
            return False
    return True


def sanitize_value(value: Any, *, depth: int = 0, max_depth: int = 8) -> Any:
    """Recursively sanitize structures, masking sensitive keys and token patterns."""
    if depth > max_depth:
        return "… [структура усечена по глубине]"

    if isinstance(value, Mapping):
        cleaned_dict: dict[str, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            if SENSITIVE_KEY_RE.search(key_str):
                cleaned_dict[key_str] = REDACTED_MARKER
            else:
                cleaned_dict[key_str] = sanitize_value(v, depth=depth + 1, max_depth=max_depth)
        return cleaned_dict

    if isinstance(value, (list, tuple, set)):
        items = list(value)[:100]  # Ограничиваем длину списков
        return [sanitize_value(item, depth=depth + 1, max_depth=max_depth) for item in items]

    if isinstance(value, str):
        text = value
        for pattern in SECRET_TEXT_PATTERNS:
            text = pattern.sub(lambda m: f"{m.group(1)}: {REDACTED_MARKER}" if m.lastindex == 2 else REDACTED_MARKER, text)
        if len(text) > 1500:
            return text[:1500] + "… [строка усечена]"
        return text

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()

    return str(value)


def enforce_length_limit(text: str, max_length: int = MAX_COMMENT_LENGTH) -> str:
    """Ensure comment doesn't exceed maximum length for IntraService."""
    if len(text) <= max_length:
        return text

    trunc_notice = "\n\n... [дамп усечен до лимита API]"
    close_code_block = "\n```"

    # Резервируем место под закрывающий блок кода
    cutoff_with_code = max_length - len(trunc_notice) - len(close_code_block)
    truncated = text[:cutoff_with_code]

    if truncated.count("```") % 2 != 0:
        return truncated + trunc_notice + close_code_block

    cutoff = max_length - len(trunc_notice)
    return text[:cutoff] + trunc_notice


class AutopilotReporter:
    """Service for formatting DLP-safe internal comments for IntraService tickets."""

    @classmethod
    def format_start_comment(
        cls,
        *,
        run_id: str | Any | None = None,
        task_id: int | str,
        scenario_key: str,
        scenario_version: int = 1,
        depth: str = "applied",
        target_host: dict[str, Any] | str | None = None,
        confidence: float | None = None,
        facts: dict[str, Any] | None = None,
    ) -> str:
        """Format comment for trigger 'on_start'."""
        title = human_scenario_title(scenario_key)
        normalized_depth = (depth or "applied").strip().lower()

        if normalized_depth == "applied":
            host_str = ""
            if isinstance(target_host, dict):
                h_name = target_host.get("name") or target_host.get("hostname") or target_host.get("host")
                h_ip = target_host.get("ip")
                rtt = target_host.get("rtt_ms") or target_host.get("rtt")
                parts = []
                if h_name:
                    parts.append(f"`{h_name}`")
                if h_ip:
                    parts.append(f"IP: {h_ip}")
                if rtt:
                    parts.append(f"отклик {rtt} мс")
                if parts:
                    host_str = f" Целевой ПК: {', '.join(parts)}."
            elif isinstance(target_host, str) and target_host.strip():
                host_str = f" Целевой ПК: `{target_host.strip()}`."

            comment = (
                f"[IntraLink Autopilot: Старт]\n"
                f"Заявка принята в автоматическую обработку по сценарию «{title}».{host_str}"
            )
            return enforce_length_limit(comment)

        # technical mode
        payload = {
            "event": "on_start",
            "run_id": str(run_id) if run_id else None,
            "task_id": int(task_id),
            "scenario_key": scenario_key,
            "scenario_version": scenario_version,
            "confidence": round(confidence, 3) if confidence is not None else None,
            "target_host": sanitize_value(target_host),
            "facts": sanitize_value(facts or {}),
            "timestamp": dt.datetime.now(UTC).isoformat(),
        }
        sanitized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        comment = (
            f"[IntraLink Autopilot: Trace]\n"
            f"Событие: on_start | Сценарий: {scenario_key} (v{scenario_version}) | Время: {payload['timestamp']}\n\n"
            f"```json\n{sanitized_json}\n```"
        )
        return enforce_length_limit(comment)

    @classmethod
    def format_pause_comment(
        cls,
        *,
        run_id: str | Any | None = None,
        task_id: int | str,
        scenario_key: str,
        scenario_version: int = 1,
        depth: str = "applied",
        reason: str | None = None,
        missing_facts: list[str] | dict[str, Any] | None = None,
        error_detail: str | dict[str, Any] | None = None,
        facts: dict[str, Any] | None = None,
    ) -> str:
        """Format comment for trigger 'on_pause_or_error'."""
        normalized_depth = (depth or "applied").strip().lower()
        clean_reason = (reason or "").strip() or "Требуется уточнение данных или вмешательство оператора"

        if normalized_depth == "applied":
            details = []
            if missing_facts:
                if isinstance(missing_facts, list):
                    facts_names = ", ".join(f"`{f}`" for f in missing_facts[:5])
                    details.append(f"Недостающие параметры: {facts_names}.")
                elif isinstance(missing_facts, dict):
                    facts_names = ", ".join(f"`{k}`" for k in list(missing_facts.keys())[:5])
                    details.append(f"Недостающие параметры: {facts_names}.")
            if error_detail and isinstance(error_detail, str):
                details.append(f"Диагностика: {sanitize_value(error_detail)}.")

            details_str = f" {' '.join(details)}" if details else ""
            comment = (
                f"[IntraLink Autopilot: Пауза]\n"
                f"Выполнение приостановлено: {clean_reason}.{details_str}"
            )
            return enforce_length_limit(comment)

        # technical mode
        payload = {
            "event": "on_pause_or_error",
            "run_id": str(run_id) if run_id else None,
            "task_id": int(task_id),
            "scenario_key": scenario_key,
            "scenario_version": scenario_version,
            "reason": clean_reason,
            "missing_facts": sanitize_value(missing_facts),
            "error_detail": sanitize_value(error_detail),
            "facts": sanitize_value(facts or {}),
            "timestamp": dt.datetime.now(UTC).isoformat(),
        }
        sanitized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        comment = (
            f"[IntraLink Autopilot: Trace]\n"
            f"Событие: on_pause_or_error | Сценарий: {scenario_key} | Время: {payload['timestamp']}\n\n"
            f"```json\n{sanitized_json}\n```"
        )
        return enforce_length_limit(comment)

    @classmethod
    def format_complete_comment(
        cls,
        *,
        run_id: str | Any | None = None,
        task_id: int | str,
        scenario_key: str,
        scenario_version: int = 1,
        depth: str = "applied",
        outcome: str | None = None,
        proof: dict[str, Any] | None = None,
        duration_seconds: float | None = None,
    ) -> str:
        """Format comment for trigger 'on_complete'."""
        title = human_scenario_title(scenario_key)
        normalized_depth = (depth or "applied").strip().lower()

        if normalized_depth == "applied":
            clean_outcome = (outcome or "").strip() or "Все операции успешно завершены и верифицированы."
            duration_str = f" Время выполнения: {round(duration_seconds, 1)} сек." if duration_seconds is not None else ""
            comment = (
                f"[IntraLink Autopilot: Успех]\n"
                f"Сценарий «{title}» успешно завершён. {clean_outcome}{duration_str}"
            )
            return enforce_length_limit(comment)

        # technical mode
        payload = {
            "event": "on_complete",
            "run_id": str(run_id) if run_id else None,
            "task_id": int(task_id),
            "scenario_key": scenario_key,
            "scenario_version": scenario_version,
            "outcome": outcome,
            "execution_proof": sanitize_value(proof or {}),
            "duration_seconds": round(duration_seconds, 2) if duration_seconds is not None else None,
            "timestamp": dt.datetime.now(UTC).isoformat(),
        }
        sanitized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        comment = (
            f"[IntraLink Autopilot: Trace]\n"
            f"Событие: on_complete | Сценарий: {scenario_key} | Время: {payload['timestamp']}\n\n"
            f"```json\n{sanitized_json}\n```"
        )
        return enforce_length_limit(comment)
