"""Fail-closed validation for user-facing decision responses."""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from pydantic import Field

from shared.domain import DecisionResponse
from shared.domain.models import StrictModel


MAX_RESPONSE_LENGTH = 900
FORBIDDEN_MARKERS = (
    "исторический прецедент",
    "запрещено утверждать",
    "планируемое инфраструктурное действие",
    "данные телеметрии хоста",
    "подтвержденный факт выполнения",
    "следующие реплики",
    "system prompt",
    "responseplan",
)
PLACEHOLDER_RE = re.compile(
    r"(?:\?GB|Spooler\s*:\s*\?|\b(?:unknown|null|undefined|n/?a)\b|\{\{[^}]+\}\})",
    re.IGNORECASE,
)
UNVERIFIED_COMPLETION_RE = re.compile(
    r"\b(?:я|мы)\s+(?:проверил(?:и)?|перезагрузил(?:и)?|установил(?:и)?|"
    r"настроил(?:и)?|исправил(?:и)?|решил(?:и)?|выполнил(?:и)?|создал(?:и)?|"
    r"предоставил(?:и)?)\b|\b(?:успешно\s+выполнен[ао]?|проблема\s+решена)\b",
    re.IGNORECASE,
)
PC_RE = re.compile(r"\b[A-ZА-Я]{2,8}[\s-]?\d{3,5}\b", re.IGNORECASE)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


class GeneratedResponse(StrictModel):
    text: str = Field(min_length=1, max_length=MAX_RESPONSE_LENGTH)
    used_evidence_refs: list[str] = Field(default_factory=list)


def _fact_values(facts_summary: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for item in facts_summary.values():
        if not isinstance(item, dict) or item.get("state") != "valid":
            continue
        value = item.get("value")
        if value not in (None, "", "<redacted>"):
            values.add(str(value).strip().upper().replace(" ", ""))
    return values


def validate_response(
    *,
    text: str,
    used_evidence_refs: list[str] | None,
    allowed_evidence_refs: list[str],
    facts_summary: dict[str, Any],
    verified_execution: bool = False,
) -> list[str]:
    candidate = (text or "").strip()
    violations: list[str] = []
    lowered = candidate.lower()
    if not candidate:
        violations.append("response_empty")
    if len(candidate) > MAX_RESPONSE_LENGTH:
        violations.append("response_too_long")
    if any(marker in lowered for marker in FORBIDDEN_MARKERS):
        violations.append("prompt_leakage")
    if PLACEHOLDER_RE.search(candidate):
        violations.append("unresolved_placeholder")
    if not verified_execution and UNVERIFIED_COMPLETION_RE.search(candidate):
        violations.append("unsupported_completion_claim")

    allowed_refs = set(allowed_evidence_refs)
    if not set(used_evidence_refs or []).issubset(allowed_refs):
        violations.append("unknown_evidence_ref")

    facts = _fact_values(facts_summary)
    for raw_pc in PC_RE.findall(candidate):
        normalized = raw_pc.upper().replace(" ", "").replace("-", "")
        if normalized not in {value.replace("-", "") for value in facts}:
            violations.append("ungrounded_pc_name")
            break
    for raw_ip in IP_RE.findall(candidate):
        try:
            normalized_ip = str(ipaddress.ip_address(raw_ip))
        except ValueError:
            violations.append("invalid_ip_address")
            break
        if normalized_ip.upper() not in facts:
            violations.append("ungrounded_ip_address")
            break
    return list(dict.fromkeys(violations))


def guarded_response(
    *,
    generated: GeneratedResponse | None,
    template_text: str,
    allowed_evidence_refs: list[str],
    facts_summary: dict[str, Any],
    verified_execution: bool = False,
) -> DecisionResponse:
    if generated is not None:
        violations = validate_response(
            text=generated.text,
            used_evidence_refs=generated.used_evidence_refs,
            allowed_evidence_refs=allowed_evidence_refs,
            facts_summary=facts_summary,
            verified_execution=verified_execution,
        )
        if not violations:
            return DecisionResponse(
                text=generated.text.strip(),
                mode="llm",
                state="valid",
                used_evidence_refs=generated.used_evidence_refs,
            )

    template_violations = validate_response(
        text=template_text,
        used_evidence_refs=[],
        allowed_evidence_refs=allowed_evidence_refs,
        facts_summary=facts_summary,
        verified_execution=verified_execution,
    )
    if not template_violations:
        return DecisionResponse(
            text=template_text.strip(),
            mode="fallback" if generated is not None else "template",
            state="fallback" if generated is not None else "valid",
            violations=(
                validate_response(
                    text=generated.text,
                    used_evidence_refs=generated.used_evidence_refs,
                    allowed_evidence_refs=allowed_evidence_refs,
                    facts_summary=facts_summary,
                    verified_execution=verified_execution,
                )
                if generated is not None
                else []
            ),
        )
    return DecisionResponse(
        text="",
        mode="none",
        state="invalid",
        violations=template_violations,
    )
