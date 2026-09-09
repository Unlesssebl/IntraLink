"""Read-only collectors that convert ticket inputs to typed observations."""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.domain import FactObservation, FactSource, FactState, PersonCandidate, validate_person_candidate
from shared.normalizer import (
    extract_pc_names_from_text,
    extract_printer_addresses_from_text,
    is_valid_pc_name,
    is_valid_printer_name,
    normalize_printer_address,
)

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
PRINTER_MODEL_RE = re.compile(
    r"\b(?:HP|Canon|Kyocera|Samsung|Xerox|Epson|Brother|Ricoh|Pantum|LaserJet|ECOSYS|COMPA)"
    r"[^,;\r\n()]{0,96}",
    re.IGNORECASE,
)


def _clean_printer_model(value: str) -> str:
    value = IP_RE.sub("", value)
    token_pattern = re.compile(r"(?i)\b([a-zа-яё]{2,6})[\s\-_]?([0-9]{2,6})\b")
    for m in token_pattern.finditer(value):
        norm = normalize_printer_address(f"{m.group(1)} {m.group(2)}") or normalize_printer_address(f"{m.group(1)}{m.group(2)}")
        if norm and is_valid_printer_name(norm):
            value = value[:m.start()] + " " + value[m.end():]
    value = re.split(r"\s+(?:к|на)\s+компьютер", value, maxsplit=1, flags=re.IGNORECASE)[0]
    value = re.split(r"\s+(?:с\s+Winscan|и\s+принтер)", value, maxsplit=1, flags=re.IGNORECASE)[0]
    return " ".join(value.strip(" .:-()\t").split())


def _printer_targets(text: str) -> list[dict[str, str | None]]:
    targets: list[dict[str, str | None]] = []
    scan_text = re.sub(
        r"\s+и\s+(?=(?:принтер|мфу))", ", ", text, flags=re.IGNORECASE
    )
    wired_local = bool(
        re.search(r"провод\w*\s+.*подключ", text, re.IGNORECASE)
    )
    for match in PRINTER_MODEL_RE.finditer(scan_text):
        model = _clean_printer_model(match.group(0))
        if not model:
            continue
        tail = re.split(r"[,;\r\n]", scan_text[match.end() : match.end() + 80], maxsplit=1)[0]
        ip_match = IP_RE.search(tail)
        local_context = f"{match.group(0)} {tail}"
        addrs = extract_printer_addresses_from_text(local_context)
        resolved_addr = ip_match.group(0) if ip_match else (addrs[0] if addrs else None)
        connection = "usb" if re.search(r"\busb\b", local_context, re.IGNORECASE) or (wired_local and not resolved_addr) else (
            "network" if resolved_addr else None
        )
        target = {
            "printer_name": model,
            "printer_address": resolved_addr,
            "connection_type": connection,
        }
        existing = next(
            (
                item
                for item in targets
                if item["printer_name"].casefold() == model.casefold()
            ),
            None,
        )
        if existing is not None:
            existing["printer_address"] = existing["printer_address"] or target["printer_address"]
            existing["connection_type"] = existing["connection_type"] or target["connection_type"]
        else:
            targets.append(target)
    return targets


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
    pc_field = raw.get(str(settings.PRINTER_PC_CUSTOM_FIELD_ID)) or raw.get(settings.PRINTER_PC_CUSTOM_FIELD_ID)
    printer_field = raw.get(str(settings.PRINTER_IP_CUSTOM_FIELD_ID)) or raw.get(settings.PRINTER_IP_CUSTOM_FIELD_ID)
    field_1104 = raw.get("1104") or raw.get(1104)
    field_1111 = raw.get("1111") or raw.get(1111)

    if pc_field and is_valid_pc_name(str(pc_field).strip()):
        result.append(_observation(
            "pc_name", pc_field, source=FactSource.STRUCTURED_FIELD,
            source_ref=f"field:{settings.PRINTER_PC_CUSTOM_FIELD_ID}",
        ))

    if field_1104:
        addrs_1104 = extract_printer_addresses_from_text(str(field_1104).strip())
        if addrs_1104:
            result.append(_observation(
                "printer_address",
                addrs_1104[0],
                source=FactSource.STRUCTURED_FIELD,
                source_ref="field:1104",
                evidence_span=str(field_1104).strip(),
            ))

    if printer_field:
        printer_value = str(printer_field).strip()
        addrs = extract_printer_addresses_from_text(printer_value)
        if addrs:
            result.append(_observation(
                "printer_address",
                addrs[0],
                source=FactSource.STRUCTURED_FIELD,
                source_ref=f"field:{settings.PRINTER_IP_CUSTOM_FIELD_ID}",
                evidence_span=addrs[0],
            ))
            clean_name = _clean_printer_model(printer_value)
            if clean_name:
                result.append(_observation(
                    "printer_name",
                    clean_name,
                    source=FactSource.STRUCTURED_FIELD,
                    source_ref=f"field:{settings.PRINTER_IP_CUSTOM_FIELD_ID}",
                    evidence_span=clean_name,
                ))
        else:
            result.append(_observation(
                "printer_name",
                printer_value,
                source=FactSource.STRUCTURED_FIELD,
                source_ref=f"field:{settings.PRINTER_IP_CUSTOM_FIELD_ID}",
                evidence_span=printer_value,
            ))
    elif field_1111:
        f1111_val = str(field_1111).strip()
        addrs_1111 = extract_printer_addresses_from_text(f1111_val)
        if addrs_1111:
            result.append(_observation(
                "printer_address",
                addrs_1111[0],
                source=FactSource.STRUCTURED_FIELD,
                source_ref="field:1111",
                evidence_span=addrs_1111[0],
            ))
        clean_1111 = _clean_printer_model(f1111_val)
        if clean_1111:
            result.append(_observation(
                "printer_name",
                clean_1111,
                source=FactSource.STRUCTURED_FIELD,
                source_ref="field:1111",
                evidence_span=clean_1111,
            ))
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

    pcs = [pc for pc in extract_pc_names_from_text(ticket_text) if not IP_RE.fullmatch(pc)]
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
    ips = list(dict.fromkeys(IP_RE.findall(ticket_text)))
    ip = IP_RE.search(ticket_text)
    addrs = extract_printer_addresses_from_text(ticket_text)
    if addrs:
        result.append(
            _observation(
                "printer_address",
                addrs[0],
                source=FactSource.PARSER,
                source_ref="parser:ticket_text:printer_address",
                evidence_span=addrs[0],
            )
        )
    elif ip:
        result.append(
            _observation(
                "printer_address",
                ip.group(0),
                source=FactSource.PARSER,
                source_ref="parser:ticket_text:ip",
                evidence_span=ip.group(0),
            )
        )
    targets = _printer_targets(ticket_text)
    if targets:
        model_text = str(targets[0]["printer_name"])
        result.append(
            _observation(
                "printer_name",
                model_text,
                source=FactSource.PARSER,
                source_ref="parser:ticket_text:printer_name",
                evidence_span=model_text,
            )
        )
        result.append(_observation(
            "printer_targets", targets, source=FactSource.PARSER,
            source_ref="parser:ticket_text:printer_targets",
            evidence_span="; ".join(
                f"{target['printer_name']} {target['printer_address'] or ''}".strip()
                for target in targets
            ),
        ))
    connection_type = (
        "mixed"
        if (ips or addrs) and re.search(r"\busb\b", ticket_text, re.IGNORECASE)
        else "usb"
        if re.search(r"\busb\b|локальн(?:ый|ого)\s+принтер|провод\w*\s+.*подключ", ticket_text, re.IGNORECASE)
        else "network"
        if (ip or addrs) or re.search(r"сетев(?:ой|ого)\s+(?:принтер|мфу)", ticket_text, re.IGNORECASE)
        else None
    )
    if connection_type:
        result.append(
            _observation(
                "printer_connection_type",
                connection_type,
                source=FactSource.PARSER,
                source_ref="parser:ticket_text:printer_connection_type",
                evidence_span=connection_type,
            )
        )
    attachments = task.get("Attachments") or task.get("attachments") or []
    mentions_attachment = bool(
        re.search(r"\b(?:скрин|скриншот|фото|вложен|прикреп)\w*", ticket_text, re.IGNORECASE)
    )
    if attachments or mentions_attachment:
        result.append(
            _observation(
                "attachments_state",
                "available" if attachments else "missing",
                source=FactSource.STRUCTURED_FIELD,
                source_ref="ticket:attachments",
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
        comment_addrs = extract_printer_addresses_from_text(text)
        if comment_addrs:
            result.append(
                _observation(
                    "printer_address",
                    comment_addrs[0],
                    source=FactSource.COMMENT,
                    source_ref=f"{comment_ref}:printer_address",
                    evidence_span=comment_addrs[0],
                )
            )
        else:
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
        "printer_name": proposal.get("printer_name") or enriched.get("_extracted_printer_name"),
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
