"""Autopilot internal audit reporter and DLP sanitization engine.

Formats non-intrusive internal service comments (IsPrivateComment = true)
for IntraService tickets across four key lifecycle events:
- on_start: ticket claimed by autopilot, extracted entities (PC, login)
- on_diagnostic: host socket probe results (FastSocketProbe ports: 5985, 445, 9100)
- on_complete: scenario execution success with proof (WinRM exit code, LDAP DN)
- on_pause_or_escalate: suspension (Status 6) or escalation to human engineer (Status 2)

Supports two depth levels:
- applied: 1-2 concise, clear Russian sentences for helpdesk engineers
- technical: structured, sanitized JSON trace dump for administrators/auditors
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any, Dict, List, Mapping, Optional, Union

from core.diagnostic.ports import FastProbeResult
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO

logger = logging.getLogger("core.scenarios.reporter")

UTC = dt.timezone.utc

MAX_COMMENT_LENGTH = 3500
REDACTED_MARKER = "***REDACTED***"

SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|authorization|credential|api_key|auth_b64|private_key|access_token|refresh_token|it_password|user_password)",
    re.IGNORECASE,
)

SECRET_TEXT_PATTERNS = [
    re.compile(r"(?i)\b(password|пароль|secret|токен|token|api_key|auth_b64|it_password)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)\bBearer\s+([a-zA-Z0-9_\-\.]{15,})\b"),
    re.compile(r"(?i)\bBasic\s+([a-zA-Z0-9+/=]{15,})\b"),
]

SCENARIO_TITLES: Dict[str, str] = {
    "install_printer": "Установка сетевого принтера",
    "printer_install": "Установка сетевого принтера",
    "default_printer_fix": "Назначение принтера по умолчанию",
    "printer_spooler_restart": "Перезапуск диспетчера печати Spooler",
    "spooler_restart": "Перезапуск диспетчера печати Spooler",
    "ad_password_reset": "Сброс пароля в Active Directory",
    "account_create": "Создание учетной записи сотрудника",
    "account_lock": "Блокировка учетной записи",
    "rag_consultation": "Консультация из базы знаний",
    "directum_access": "Выдача доступа DIRECTUM",
    "sbis_setup": "Настройка СБИС / ЭДО",
    "cancel_duplicate": "Отмена дубликата заявки",
    "redirect_service": "Перенаправление заявки в корректный сервис",
}


def human_scenario_title(scenario_key: Optional[str]) -> str:
    """Return human-readable Russian scenario title."""
    if not scenario_key:
        return "Автоматический сценарий"
    key = str(scenario_key).strip().lower()
    return SCENARIO_TITLES.get(key, key.replace("_", " ").capitalize())


def sanitize_value(value: Any, *, depth: int = 0, max_depth: int = 8) -> Any:
    """Recursively sanitize structures, masking sensitive keys and token patterns."""
    if depth > max_depth:
        return "… [структура усечена по глубине]"

    if isinstance(value, Mapping):
        cleaned_dict: Dict[str, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            if SENSITIVE_KEY_RE.search(key_str):
                cleaned_dict[key_str] = REDACTED_MARKER
            else:
                cleaned_dict[key_str] = sanitize_value(v, depth=depth + 1, max_depth=max_depth)
        return cleaned_dict

    if isinstance(value, (list, tuple, set)):
        items = list(value)[:100]
        return [sanitize_value(item, depth=depth + 1, max_depth=max_depth) for item in items]

    if isinstance(value, str):
        text = value
        for pattern in SECRET_TEXT_PATTERNS:
            text = pattern.sub(
                lambda m: f"{m.group(1)}: {REDACTED_MARKER}" if m.lastindex == 2 else REDACTED_MARKER,
                text,
            )
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

    cutoff_with_code = max_length - len(trunc_notice) - len(close_code_block)
    truncated = text[:cutoff_with_code]

    if truncated.count("```") % 2 != 0:
        return truncated + trunc_notice + close_code_block

    cutoff = max_length - len(trunc_notice)
    return text[:cutoff] + trunc_notice


class AutopilotReporter:
    """Service for formatting and publishing DLP-safe internal audit notes for IntraService."""

    @classmethod
    def format_start_comment(
        cls,
        *,
        task_id: int | str,
        scenario_key: str,
        scenario_name: Optional[str] = None,
        depth: str = "applied",
        target_host: Optional[Union[Dict[str, Any], str]] = None,
        facts: Optional[Union[Dict[str, Any], ExtractedEntitiesDTO]] = None,
        confidence: Optional[float] = None,
    ) -> str:
        """Format hidden audit note for trigger 'on_start'."""
        title = scenario_name or human_scenario_title(scenario_key)
        normalized_depth = (depth or "applied").strip().lower()

        facts_dict: Dict[str, Any] = {}
        if isinstance(facts, ExtractedEntitiesDTO):
            facts_dict = facts.model_dump()
        elif isinstance(facts, dict):
            facts_dict = dict(facts)

        sanitized_facts = sanitize_value(facts_dict)

        if normalized_depth == "applied":
            details: List[str] = []
            pc = facts_dict.get("pc_name")
            user = facts_dict.get("target_user") or facts_dict.get("user_name")
            printer = facts_dict.get("printer_address") or facts_dict.get("printer_model")

            if pc:
                details.append(f"ПК: `{pc}`")
            elif target_host:
                h_name = target_host.get("name") if isinstance(target_host, dict) else str(target_host)
                details.append(f"ПК: `{h_name}`")
            if user:
                details.append(f"Пользователь: `{user}`")
            if printer:
                details.append(f"Принтер: `{printer}`")
            if confidence is not None:
                details.append(f"Уверенность: {confidence:.0%}")

            details_str = f" Реквизиты: {', '.join(details)}." if details else ""
            comment = (
                f"🤖 [IntraLink Autopilot: Старт]\n"
                f"Заявка принята в автоматическую обработку по сценарию «{title}».{details_str}"
            )
            return enforce_length_limit(comment)

        # technical mode
        payload = {
            "event": "on_start",
            "task_id": int(task_id),
            "scenario_key": scenario_key,
            "scenario_name": title,
            "confidence": round(confidence, 3) if confidence is not None else None,
            "target_host": sanitize_value(target_host),
            "facts": sanitized_facts,
            "timestamp": dt.datetime.now(UTC).isoformat(),
        }
        sanitized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        comment = (
            f"🤖 [IntraLink Autopilot: Trace]\n"
            f"Событие: on_start | Сценарий: {scenario_key} | Время: {payload['timestamp']}\n\n"
            f"```json\n{sanitized_json}\n```"
        )
        return enforce_length_limit(comment)

    @classmethod
    def format_diagnostic_comment(
        cls,
        *,
        task_id: int | str,
        host: str,
        probe_result: Optional[Union[FastProbeResult, Dict[str, Any]]] = None,
        ports: Optional[Dict[Union[int, str], bool]] = None,
        depth: str = "applied",
    ) -> str:
        """Format hidden audit note for trigger 'on_diagnostic' (FastSocketProbe ports: 5985, 445, 9100)."""
        normalized_depth = (depth or "applied").strip().lower()

        is_online = False
        rtt_ms: Optional[float] = None
        ports_map: Dict[str, bool] = {}

        if isinstance(probe_result, FastProbeResult):
            is_online = probe_result.is_online
            rtt_ms = probe_result.rtt_ms
            ports_map = {str(k): v for k, v in probe_result.ports.items()}
        elif isinstance(probe_result, dict):
            is_online = bool(probe_result.get("is_online", False))
            rtt_ms = probe_result.get("rtt_ms")
            p = probe_result.get("ports") or {}
            ports_map = {str(k): v for k, v in p.items()}
        elif ports:
            ports_map = {str(k): v for k, v in ports.items()}
            is_online = any(ports_map.values())

        if normalized_depth == "applied":
            status_text = "В СЕТИ" if is_online else "НЕДОСТУПЕН"
            ports_desc: List[str] = []
            for p, ok in ports_map.items():
                p_name = {
                    "5985": "WinRM(5985)",
                    "445": "SMB(445)",
                    "9100": "RAW(9100)",
                    "135": "RPC(135)",
                }.get(str(p), f"Port({p})")
                ports_desc.append(f"{p_name}: {'ДОСТУПЕН' if ok else 'ЗАКРЫТ'}")

            rtt_text = f" (отклик {rtt_ms} мс)" if rtt_ms is not None else ""
            ports_summary = f" Проверка портов: {', '.join(ports_desc)}." if ports_desc else ""
            comment = (
                f"🤖 [IntraLink Autopilot: Диагностика сокетов]\n"
                f"Целевой хост `{host}`: {status_text}{rtt_text}.{ports_summary}"
            )
            return enforce_length_limit(comment)

        # technical mode
        payload = {
            "event": "on_diagnostic",
            "task_id": int(task_id),
            "host": host,
            "is_online": is_online,
            "rtt_ms": rtt_ms,
            "ports": ports_map,
            "timestamp": dt.datetime.now(UTC).isoformat(),
        }
        sanitized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        comment = (
            f"🤖 [IntraLink Autopilot: Trace]\n"
            f"Событие: on_diagnostic | Хост: {host} | Статус: {'ONLINE' if is_online else 'OFFLINE'}\n\n"
            f"```json\n{sanitized_json}\n```"
        )
        return enforce_length_limit(comment)

    @classmethod
    def format_complete_comment(
        cls,
        *,
        task_id: int | str,
        scenario_key: str,
        scenario_name: Optional[str] = None,
        depth: str = "applied",
        outcome: Optional[str] = None,
        proof: Optional[Dict[str, Any]] = None,
        duration_seconds: Optional[float] = None,
        target_status_id: int = 3,
    ) -> str:
        """Format hidden audit note for trigger 'on_complete'."""
        title = scenario_name or human_scenario_title(scenario_key)
        normalized_depth = (depth or "applied").strip().lower()
        clean_proof = sanitize_value(proof or {})

        if normalized_depth == "applied":
            clean_outcome = (outcome or "").strip() or "Все технологические шаги успешно выполнены и подтверждены."
            proof_parts: List[str] = []
            if isinstance(clean_proof, dict):
                if "winrm_exit_code" in clean_proof:
                    proof_parts.append(f"WinRM exit code: {clean_proof['winrm_exit_code']}")
                if "ldap_dn" in clean_proof:
                    proof_parts.append(f"LDAP DN: `{clean_proof['ldap_dn']}`")
                if "stdout_tail" in clean_proof:
                    proof_parts.append(f"Output: {clean_proof['stdout_tail'][:100]}")

            proof_str = f" Доказательства: {', '.join(proof_parts)}." if proof_parts else ""
            dur_str = f" Время: {duration_seconds:.1f}с." if duration_seconds is not None else ""
            comment = (
                f"🤖 [IntraLink Autopilot: Успех]\n"
                f"Сценарий «{title}» успешно завершён. Статус заявки переведен в {target_status_id}. "
                f"{clean_outcome}{proof_str}{dur_str}"
            )
            return enforce_length_limit(comment)

        # technical mode
        payload = {
            "event": "on_complete",
            "task_id": int(task_id),
            "scenario_key": scenario_key,
            "scenario_name": title,
            "target_status_id": target_status_id,
            "outcome": outcome,
            "execution_proof": clean_proof,
            "duration_seconds": round(duration_seconds, 2) if duration_seconds is not None else None,
            "timestamp": dt.datetime.now(UTC).isoformat(),
        }
        sanitized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        comment = (
            f"🤖 [IntraLink Autopilot: Trace]\n"
            f"Событие: on_complete | Сценарий: {scenario_key} | Статус → {target_status_id}\n\n"
            f"```json\n{sanitized_json}\n```"
        )
        return enforce_length_limit(comment)

    @classmethod
    def format_pause_or_escalate_comment(
        cls,
        *,
        task_id: int | str,
        scenario_key: str,
        target_status_id: int,  # 6 = Приостановлена, 2 = В работе (эскалация)
        depth: str = "applied",
        reason: Optional[str] = None,
        missing_facts: Optional[Union[List[str], Dict[str, Any]]] = None,
        environment_barriers: Optional[List[str]] = None,
        rounds: Optional[int] = None,
        error_detail: Optional[Union[str, Dict[str, Any]]] = None,
    ) -> str:
        """Format hidden audit note for trigger 'on_pause_or_escalate'."""
        normalized_depth = (depth or "applied").strip().lower()
        title = human_scenario_title(scenario_key)

        clean_reason = (reason or "").strip() or (
            "Ожидание ответа заявителя" if target_status_id == 6 else "Передано инженеру 1-й линии"
        )
        clean_missing = sanitize_value(missing_facts)
        clean_barriers = sanitize_value(environment_barriers or [])

        if normalized_depth == "applied":
            details: List[str] = []
            if missing_facts:
                if isinstance(missing_facts, list):
                    facts_names = ", ".join(f"`{f}`" for f in missing_facts[:5])
                    details.append(f"Отсутствуют реквизиты: {facts_names}")
                elif isinstance(missing_facts, dict):
                    facts_names = ", ".join(f"`{k}`" for k in list(missing_facts.keys())[:5])
                    details.append(f"Отсутствуют реквизиты: {facts_names}")
            if environment_barriers:
                barriers_str = ", ".join(str(b) for b in environment_barriers)
                details.append(f"Барьеры среды: {barriers_str}")
            if rounds is not None:
                details.append(f"Раунд диалога: {rounds}")
            if error_detail and isinstance(error_detail, str):
                details.append(f"Сбой: {sanitize_value(error_detail)}")

            details_str = f"\nДетали: {'; '.join(details)}." if details else ""

            if target_status_id == 6:
                header = "🤖 [IntraLink Autopilot: Приостановлена (Статус 6)]"
                action_text = f"Сценарий «{title}» приостановлен: {clean_reason}."
            else:
                header = "🤖 [IntraLink Autopilot: Эскалация инженеру (Статус 2)]"
                action_text = f"Сценарий «{title}» передан дежурному инженеру: {clean_reason}."

            comment = f"{header}\n{action_text}{details_str}"
            return enforce_length_limit(comment)

        # technical mode
        payload = {
            "event": "on_pause_or_escalate",
            "task_id": int(task_id),
            "scenario_key": scenario_key,
            "target_status_id": target_status_id,
            "reason": clean_reason,
            "missing_facts": clean_missing,
            "environment_barriers": clean_barriers,
            "rounds": rounds,
            "error_detail": sanitize_value(error_detail),
            "timestamp": dt.datetime.now(UTC).isoformat(),
        }
        sanitized_json = json.dumps(payload, ensure_ascii=False, indent=2)
        comment = (
            f"🤖 [IntraLink Autopilot: Trace]\n"
            f"Событие: on_pause_or_escalate | Статус → {target_status_id} | Сценарий: {scenario_key}\n\n"
            f"```json\n{sanitized_json}\n```"
        )
        return enforce_length_limit(comment)

    async def post_audit_comment(
        self,
        client: IntraServiceClient,
        task_id: int,
        comment: str,
        auth_b64: Optional[str] = None,
    ) -> bool:
        """Publish hidden internal audit note to IntraService with Fault Tolerance."""
        try:
            return await client.add_task_comment(
                task_id=task_id,
                comment=comment,
                is_private=True,
                auth_b64=auth_b64,
            )
        except Exception as exc:
            logger.warning(
                "Failed to post internal audit note for ticket #%d: %s. Fault-tolerant bypass.",
                task_id,
                exc,
            )
            return False

    async def report_start(
        self,
        client: IntraServiceClient,
        task_id: int,
        scenario_key: str,
        scenario_name: Optional[str] = None,
        depth: str = "applied",
        target_host: Optional[Union[Dict[str, Any], str]] = None,
        facts: Optional[Union[Dict[str, Any], ExtractedEntitiesDTO]] = None,
        confidence: Optional[float] = None,
        auth_b64: Optional[str] = None,
    ) -> bool:
        """Report scenario start event as hidden internal comment."""
        comment = self.format_start_comment(
            task_id=task_id,
            scenario_key=scenario_key,
            scenario_name=scenario_name,
            depth=depth,
            target_host=target_host,
            facts=facts,
            confidence=confidence,
        )
        return await self.post_audit_comment(client, task_id, comment, auth_b64)

    async def report_diagnostic(
        self,
        client: IntraServiceClient,
        task_id: int,
        host: str,
        probe_result: Optional[Union[FastProbeResult, Dict[str, Any]]] = None,
        ports: Optional[Dict[Union[int, str], bool]] = None,
        depth: str = "applied",
        auth_b64: Optional[str] = None,
    ) -> bool:
        """Report socket diagnostic probe result as hidden internal comment."""
        comment = self.format_diagnostic_comment(
            task_id=task_id,
            host=host,
            probe_result=probe_result,
            ports=ports,
            depth=depth,
        )
        return await self.post_audit_comment(client, task_id, comment, auth_b64)

    async def report_complete(
        self,
        client: IntraServiceClient,
        task_id: int,
        scenario_key: str,
        scenario_name: Optional[str] = None,
        depth: str = "applied",
        outcome: Optional[str] = None,
        proof: Optional[Dict[str, Any]] = None,
        duration_seconds: Optional[float] = None,
        target_status_id: int = 3,
        auth_b64: Optional[str] = None,
    ) -> bool:
        """Report scenario completion and evidence as hidden internal comment."""
        comment = self.format_complete_comment(
            task_id=task_id,
            scenario_key=scenario_key,
            scenario_name=scenario_name,
            depth=depth,
            outcome=outcome,
            proof=proof,
            duration_seconds=duration_seconds,
            target_status_id=target_status_id,
        )
        return await self.post_audit_comment(client, task_id, comment, auth_b64)

    async def report_pause_or_escalate(
        self,
        client: IntraServiceClient,
        task_id: int,
        scenario_key: str,
        target_status_id: int,
        depth: str = "applied",
        reason: Optional[str] = None,
        missing_facts: Optional[Union[List[str], Dict[str, Any]]] = None,
        environment_barriers: Optional[List[str]] = None,
        rounds: Optional[int] = None,
        error_detail: Optional[Union[str, Dict[str, Any]]] = None,
        auth_b64: Optional[str] = None,
    ) -> bool:
        """Report scenario pause (Status 6) or escalation (Status 2) as hidden internal comment."""
        comment = self.format_pause_or_escalate_comment(
            task_id=task_id,
            scenario_key=scenario_key,
            target_status_id=target_status_id,
            depth=depth,
            reason=reason,
            missing_facts=missing_facts,
            environment_barriers=environment_barriers,
            rounds=rounds,
            error_detail=error_detail,
        )
        return await self.post_audit_comment(client, task_id, comment, auth_b64)


_global_reporter: Optional[AutopilotReporter] = None


def get_autopilot_reporter() -> AutopilotReporter:
    """Singleton getter for AutopilotReporter."""
    global _global_reporter
    if _global_reporter is None:
        _global_reporter = AutopilotReporter()
    return _global_reporter
