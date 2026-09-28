"""Idempotent clarification and conservative fact merging for AD onboarding."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from core.automation.llm_transport import LLMTransport
from core.intraservice.dto import TaskLifetimeEventDTO

ONBOARDING_FACTS = (
    "last_name",
    "first_name",
    "middle_name",
    "department",
    "title",
    "phone",
    "company",
)
REQUIRED_ONBOARDING_FACTS = ("last_name", "first_name", "department", "title")
FACT_LABELS = {
    "last_name": "фамилию",
    "first_name": "имя",
    "middle_name": "отчество",
    "department": "подразделение",
    "title": "должность",
    "phone": "телефон",
    "company": "организацию",
}
_LABEL_PATTERNS = {
    "last_name": ("фамилия", "last name"),
    "first_name": ("имя", "first name"),
    "middle_name": ("отчество", "middle name"),
    "department": ("подразделение", "отдел", "department"),
    "title": ("должность", "позиция", "title"),
    "phone": ("телефон", "номер телефона", "phone"),
    "company": ("организация", "компания", "company"),
}
TEMPLATE_VERSION = "employee_onboarding_clarification_v1"
_SECRET_RE = re.compile(r"(?i)\b(password|passwd|пароль|token|secret)\b\s*[:=]?\s*\S+")


@dataclass
class FactMergeResult:
    values: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    conflicts: dict[str, list[str]] = field(default_factory=dict)


def clarification_fingerprint(task_id: int, snapshot_hash: str, missing_facts: Iterable[str]) -> str:
    payload = {
        "task_id": task_id,
        "snapshot_hash": snapshot_hash,
        "missing_facts": sorted(set(missing_facts)),
        "template_version": TEMPLATE_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def render_clarification(missing_facts: Iterable[str]) -> str:
    missing = list(dict.fromkeys(missing_facts))
    if "last_name" in missing and "first_name" in missing:
        return "Укажите, пожалуйста, полные ФИО сотрудника, для которого необходимо создать учётную запись."
    labels = [FACT_LABELS.get(key, key) for key in missing]
    return "Для создания учётной записи дополнительно укажите, пожалуйста: " + ", ".join(labels) + "."


def extract_labeled_facts(text: str, allowed_facts: Iterable[str]) -> dict[str, dict[str, str]]:
    """Extract only literal labelled spans; never infer an unstated value."""
    allowed = set(allowed_facts) & set(ONBOARDING_FACTS)
    result: dict[str, dict[str, str]] = {}
    for raw_line in re.split(r"[\r\n;]+", text):
        line = raw_line.strip(" \t-*•")
        if not line:
            continue
        for key, labels in _LABEL_PATTERNS.items():
            if key not in allowed:
                continue
            pattern = rf"^(?:{'|'.join(re.escape(label) for label in labels)})\s*[:\-–—]\s*(.+?)\s*$"
            match = re.match(pattern, line, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if value:
                    result[key] = {"value": value, "text_span": match.group(0)}
                break

    # The dedicated first-round FIO template accepts a literal three/two-part reply.
    if {"last_name", "first_name"}.issubset(allowed) and not ({"last_name", "first_name"} & result.keys()):
        compact = " ".join(text.split())
        if re.fullmatch(r"[A-Za-zА-Яа-яЁё-]+(?:\s+[A-Za-zА-Яа-яЁё-]+){1,2}", compact):
            parts = compact.split()
            result["last_name"] = {"value": parts[0], "text_span": compact}
            result["first_name"] = {"value": parts[1], "text_span": compact}
            if len(parts) == 3 and "middle_name" in allowed:
                result["middle_name"] = {"value": parts[2], "text_span": compact}
    return result


def merge_onboarding_facts(
    structured: dict[str, str],
    responses: Iterable[dict[str, Any]],
) -> FactMergeResult:
    merged = FactMergeResult()

    def add(key: str, value: Any, source: dict[str, Any]) -> None:
        clean = " ".join(str(value or "").split())
        if key not in ONBOARDING_FACTS or not clean:
            return
        merged.provenance.setdefault(key, []).append({**source, "value": clean})
        if source.get("source") == "operator_correction":
            merged.values[key] = clean
            merged.conflicts.pop(key, None)
            return
        current = merged.values.get(key)
        if current is None:
            merged.values[key] = clean
        elif current.casefold() != clean.casefold():
            variants = merged.conflicts.setdefault(key, [current])
            if clean.casefold() not in {item.casefold() for item in variants}:
                variants.append(clean)

    for key in ONBOARDING_FACTS:
        add(key, structured.get(key), {"source": "structured_field"})
    for response in responses:
        event_id = response.get("event_id")
        for key, item in (response.get("facts") or {}).items():
            if isinstance(item, dict):
                add(
                    key,
                    item.get("value"),
                    {
                        "source": response.get("source", "public_comment"),
                        "event_id": event_id,
                        "text_span": item.get("text_span"),
                    },
                )
            else:
                add(key, item, {"source": response.get("source", "public_comment"), "event_id": event_id})
    return merged


def latest_human_public_event(
    events: Iterable[TaskLifetimeEventDTO], *, baseline_event_id: int | None, bot_user_id: int
) -> TaskLifetimeEventDTO | None:
    candidates = [
        event
        for event in events
        if event.id is not None
        and event.id > (baseline_event_id or 0)
        and not event.is_private
        and bool((event.comment or "").strip())
        and event.editor_id is not None
        and event.editor_id != bot_user_id
    ]
    return max(candidates, key=lambda item: int(item.id or 0), default=None)


class ClarificationFactExtractor:
    """Bounded LLM fallback that accepts only grounded values for requested keys."""

    def __init__(self, transport: LLMTransport, model_alias: str = "helpdesk-fast") -> None:
        self.transport = transport
        self.model_alias = model_alias

    async def extract(self, text: str, missing_facts: Iterable[str]) -> dict[str, dict[str, str]]:
        allowed = sorted(set(missing_facts) & set(ONBOARDING_FACTS))
        deterministic = extract_labeled_facts(text, allowed)
        remaining = [key for key in allowed if key not in deterministic]
        if not remaining:
            return deterministic
        sanitized = _SECRET_RE.sub("[SECRET]", text)[:4000]
        raw = await self.transport.complete_json(
            model_alias=self.model_alias,
            system_prompt=(
                "Extract only explicitly stated employee facts for allowed_keys. "
                "Return JSON {facts:[{key,value,text_span}]}. text_span must be an exact literal substring "
                "of source_text. Do not infer, normalize, translate, or return any unrequested key."
            ),
            payload={"allowed_keys": remaining, "source_text": sanitized},
            timeout_seconds=15.0,
        )
        parsed = json.loads(raw)
        items = parsed.get("facts")
        if not isinstance(items, list):
            raise ValueError("invalid_clarification_extraction")
        result = dict(deterministic)
        for item in items:
            if not isinstance(item, dict) or set(item) != {"key", "value", "text_span"}:
                raise ValueError("invalid_clarification_fact_schema")
            key = str(item["key"])
            value = " ".join(str(item["value"]).split())
            span = str(item["text_span"])
            if key not in remaining or not value or not span or span not in sanitized or value not in span:
                raise ValueError("ungrounded_clarification_fact")
            result[key] = {"value": value, "text_span": span}
        return result
