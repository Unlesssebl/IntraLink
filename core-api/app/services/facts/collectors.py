"""Read-only collectors that convert ticket inputs to typed observations."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.domain import FactObservation, FactSource, FactState, PersonCandidate, validate_person_candidate
from shared.normalizer import extract_pc_names_from_text

from app.config import settings
from app.services.fact_extractor import enrich_task_with_extracted_facts
from app.services.facts.registry import get_fact_registry
from app.utils.ad_utils import extract_user_creation_details_from_task


PERSON_FIELD_IDS: dict[str, tuple[str, ...]] = {
    "surname": ("1057", "1069"),
    "name": ("1058", "1070"),
    "patronymic": ("1059", "1071"),
    "title": ("1065", "1073"),
    "phone": ("1066", "1075"),
    "department": ("1064", "1078"),
    "pc_name": ("1068", "1120"),
    "company": ("1074",),
}
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
UNC_RE = re.compile(r"\\\\[^\s\\]+\\[^\s]+")


def _comment_source_ref(comment: dict[str, Any], text: str) -> str:
    comment_id = (
        comment.get("Id")
        or comment.get("id")
        or comment.get("CommentId")
        or comment.get("comment_id")
    )
    if comment_id not in (None, ""):
        return f"comment:{comment_id}"
    author = str(
        comment.get("Author")
        or comment.get("AuthorName")
        or comment.get("Creator")
        or comment.get("UserName")
        or ""
    )
    created_at = str(
        comment.get("CreatedAt")
        or comment.get("Created")
        or comment.get("CreateDate")
        or comment.get("Date")
        or comment.get("created_at")
        or ""
    )
    normalized = " ".join(text.casefold().split())
    digest = hashlib.sha256(
        f"{normalized}\n{author.casefold().strip()}\n{created_at.strip()}".encode("utf-8")
    ).hexdigest()[:32]
    return f"comment:sha256:{digest}"


def _observation(
    key: str,
    value: Any,
    *,
    source: FactSource,
    source_ref: str,
    evidence_span: str | None = None,
    state: FactState | None = None,
    shadow: bool = False,
) -> FactObservation:
    spec = get_fact_registry().require(key)
    normalized = spec.normalize(value) if value not in (None, "") else value
    effective_state = state or (
        FactState.VALID if normalized not in (None, "", [], {}) else FactState.MISSING
    )
    expires_at = None
    if spec.ttl_seconds:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=spec.ttl_seconds)).isoformat()
    return FactObservation(
        key=key,
        value=normalized,
        state=effective_state,
        source=source,
        source_ref=source_ref,
        evidence_span=evidence_span,
        sensitivity=spec.sensitivity,
        observed_at=datetime.now(timezone.utc).isoformat(),
        expires_at=expires_at,
        metadata={"shadow": True} if shadow else {},
    )


async def collect_structured(task: dict[str, Any]) -> list[FactObservation]:
    result: list[FactObservation] = []
    scalar_fields = {
        "task_id": task.get("Id") or task.get("id"),
        "service_id": task.get("ServiceId") or task.get("service_id"),
        "subject": task.get("Name") or task.get("name"),
        "description": task.get("Description") or task.get("description"),
        "service_name": task.get("ServiceName") or task.get("service_name"),
        "identity": (
            task.get("RequesterLogin")
            or task.get("InitiatorLogin")
            or task.get("UserLogin")
            or task.get("CreatorLogin")
            or task.get("Email")
            or task.get("Creator")
        ),
        "printer_name": task.get("PrinterName") or task.get("PrinterModel"),
    }
    for key, value in scalar_fields.items():
        if value not in (None, ""):
            result.append(
                _observation(
                    key,
                    value,
                    source=FactSource.STRUCTURED_FIELD,
                    source_ref=f"ticket:{key}",
                )
            )

    raw = ((task.get("_field_meta") or {}).get("raw") or {})
    raw_person: dict[str, Any] = {}
    raw_refs: dict[str, str] = {}
    for key, field_ids in PERSON_FIELD_IDS.items():
        for field_id in field_ids:
            value = raw.get(field_id)
            if value is not None and str(value).strip():
                raw_person[key] = value
                raw_refs[key] = f"field:{field_id}"
                break
    validation = validate_person_candidate(PersonCandidate.model_validate(raw_person))
    invalid_fields = {error.field for error in validation.errors}
    for key, value in raw_person.items():
        result.append(
            _observation(
                key,
                value,
                source=FactSource.STRUCTURED_FIELD,
                source_ref=raw_refs[key],
                state=FactState.INVALID if key in invalid_fields else FactState.VALID,
            )
        )
    return result


async def collect_deterministic(
    task: dict[str, Any], comments: list[dict[str, Any]] | None
) -> list[FactObservation]:
    subject = str(task.get("Name") or "")
    description = str(task.get("Description") or "")
    ticket_text = f"{subject}\n{description}".strip()
    result: list[FactObservation] = []

    pcs = extract_pc_names_from_text(ticket_text)
    if pcs:
        result.append(
            _observation(
                "pc_name",
                pcs[0],
                source=FactSource.PARSER,
                source_ref="parser:ticket_text:pc_name",
                evidence_span=pcs[0],
            )
        )
    ip = IP_RE.search(ticket_text)
    if ip:
        result.append(
            _observation(
                "printer_address",
                ip.group(0),
                source=FactSource.PARSER,
                source_ref="parser:ticket_text:ip",
                evidence_span=ip.group(0),
            )
        )
    path = UNC_RE.search(ticket_text)
    if path:
        result.append(
            _observation(
                "file_path",
                path.group(0),
                source=FactSource.PARSER,
                source_ref="parser:ticket_text:unc",
                evidence_span=path.group(0),
            )
        )

    # Reuse the deterministic corporate parser, but keep explicit form fields
    # separate so parser values can never silently replace invalid structured data.
    parsed_person = extract_user_creation_details_from_task(task)
    raw = ((task.get("_field_meta") or {}).get("raw") or {})
    explicit_keys = {
        key
        for key, field_ids in PERSON_FIELD_IDS.items()
        if any(str(raw.get(field_id) or "").strip() for field_id in field_ids)
    }
    for key, value in parsed_person.items():
        if key in explicit_keys or value in (None, ""):
            continue
        value_text = str(value)
        if value_text.casefold() not in ticket_text.casefold():
            continue
        result.append(
            _observation(
                key,
                value,
                source=FactSource.PARSER,
                source_ref=f"parser:ticket_text:{key}",
                evidence_span=value_text,
            )
        )

    for comment in comments or []:
        text = str(
            comment.get("Comments")
            or comment.get("Comment")
            or comment.get("Description")
            or comment.get("text")
            or ""
        ).strip()
        if not text:
            continue
        comment_ref = _comment_source_ref(comment, text)
        comment_pcs = extract_pc_names_from_text(text)
        if comment_pcs:
            result.append(
                _observation(
                    "pc_name",
                    comment_pcs[0],
                    source=FactSource.COMMENT,
                    source_ref=f"{comment_ref}:pc_name",
                    evidence_span=comment_pcs[0],
                )
            )
        comment_ip = IP_RE.search(text)
        if comment_ip:
            result.append(
                _observation(
                    "printer_address",
                    comment_ip.group(0),
                    source=FactSource.COMMENT,
                    source_ref=f"{comment_ref}:ip",
                    evidence_span=comment_ip.group(0),
                )
            )
    return result


async def collect_diagnostics(diag: dict[str, Any] | None) -> list[FactObservation]:
    if not diag:
        return []
    pc_name = diag.get("pc_name") or diag.get("host")
    if not pc_name:
        return []
    return [
        _observation(
            "pc_name",
            pc_name,
            source=FactSource.DIAGNOSTIC,
            source_ref="diagnostic:host",
        )
    ]


async def collect_llm(
    task: dict[str, Any], comments: list[dict[str, Any]] | None
) -> list[FactObservation]:
    if settings.LLM_FACT_EXTRACTION_MODE.lower() not in {"shadow", "enabled"}:
        return []
    enriched = await enrich_task_with_extracted_facts(task, comments)
    extraction = enriched.get("_llm_fact_extraction") or {}
    if not extraction.get("accepted"):
        return []
    spans = {
        item.get("field"): item.get("span")
        for item in extraction.get("evidence", [])
        if isinstance(item, dict)
    }
    shadow = settings.LLM_FACT_EXTRACTION_MODE.lower() == "shadow"
    proposal = enriched.get("_llm_extracted_facts") or {}
    values: dict[str, Any] = {
        "pc_name": proposal.get("pc_name") or enriched.get("_extracted_pc_name"),
        "printer_address": proposal.get("printer_address") or enriched.get("_extracted_printer_address"),
        "file_path": proposal.get("file_path") or enriched.get("_extracted_file_path"),
        "clarification_answer": proposal.get("clarification_answer") or enriched.get("_extracted_clarification_answer"),
        "issue_summary": proposal.get("issue_summary") or enriched.get("_extracted_issue_summary"),
    }
    person = proposal.get("person") or enriched.get("_extracted_person") or {}
    values.update({key: person.get(key) for key in PERSON_FIELD_IDS})
    result: list[FactObservation] = []
    for key, value in values.items():
        if value in (None, ""):
            continue
        span = spans.get(key)
        if not span:
            continue
        result.append(
            _observation(
                key,
                value,
                source=FactSource.LLM,
                source_ref=f"llm:{key}",
                evidence_span=span,
                shadow=shadow,
            )
        )
    return result


async def collect_ticket_observations(
    task: dict[str, Any],
    *,
    comments: list[dict[str, Any]] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[FactObservation]:
    batches = await asyncio.gather(
        collect_structured(task),
        collect_deterministic(task, comments),
        collect_diagnostics(diagnostics),
        collect_llm(task, comments),
    )
    return [observation for batch in batches for observation in batch]
