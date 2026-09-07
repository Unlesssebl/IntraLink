"""Deterministic-first fact extraction with a schema-constrained dual LLM fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from copy import deepcopy
from typing import Any

from shared.domain import (
    ExtractedTicketFacts,
    PersonCandidate,
    validate_person_candidate,
)
from shared.normalizer import normalize_pc_name

from app.config import settings
from app.services.ai.hub import ai_hub
from app.services.worker import get_redis_client
from app.utils.ad_utils import extract_person_candidate_from_task

logger = logging.getLogger(__name__)

IDENTITY_FIELD_IDS = {"surname": ("1057", "1069"), "name": ("1058", "1070")}


def _has_explicit_identity(task: dict[str, Any]) -> bool:
    raw = (task.get("_field_meta") or {}).get("raw") or {}
    return any(
        str(raw.get(field_id) or "").strip()
        for ids in IDENTITY_FIELD_IDS.values()
        for field_id in ids
    )


def _llm_evidence_is_valid(extracted: ExtractedTicketFacts) -> bool:
    if not extracted.evidence:
        return False
    return all(
        item.source == "llm" and bool(item.span and item.span.strip())
        for item in extracted.evidence
    )


def _extract_comments_text(task: dict[str, Any], comments_history: list[Any] | None = None) -> tuple[str, int]:
    """Extract and normalize all comments into a formatted text stream."""
    raw_comments = comments_history or task.get("Comments") or task.get("comments") or []
    if isinstance(raw_comments, dict):
        raw_comments = (
            raw_comments.get("TaskLifetimes")
            or raw_comments.get("tasklifetimes")
            or raw_comments.get("items")
            or []
        )
    if isinstance(raw_comments, str):
        raw_comments = [{"text": raw_comments}]
    elif not isinstance(raw_comments, list):
        return "", 0

    lines: list[str] = []
    for item in raw_comments:
        if isinstance(item, str) and item.strip():
            lines.append(f"- {item.strip()}")
            continue
        if isinstance(item, dict):
            text = (
                item.get("Comments")
                or item.get("Comment")
                or item.get("Description")
                or item.get("text")
                or ""
            )
            author = (
                item.get("Editor")
                or item.get("Author")
                or item.get("UserName")
                or item.get("Creator")
                or "Участник"
            )
            date = item.get("Date") or item.get("Created") or ""
            date_str = f" ({date})" if date else ""
            if text and str(text).strip():
                lines.append(f"- {author}{date_str}: {str(text).strip()}")

    count = len(lines)
    if not lines:
        return "", 0
    return "\n[История переписки в заявке]:\n" + "\n".join(lines), count


def _build_content_hash(task_id: Any, text: str) -> str:
    digest = hashlib.sha256(f"{task_id}:{text}".encode("utf-8")).hexdigest()[:24]
    return f"fact_cache:v2:{task_id}:{digest}"


async def _fetch_from_cache(cache_key: str) -> ExtractedTicketFacts | None:
    try:
        r = get_redis_client()
        raw = await r.get(cache_key)
        if raw:
            return ExtractedTicketFacts.model_validate_json(raw)
    except Exception as exc:
        logger.debug("Redis fact cache lookup failed: %s", exc)
    return None


async def _save_to_cache(cache_key: str, extracted: ExtractedTicketFacts, ttl: int = 86400) -> None:
    try:
        r = get_redis_client()
        await r.set(cache_key, extracted.model_dump_json(), ex=ttl)
    except Exception as exc:
        logger.debug("Redis fact cache save failed: %s", exc)


async def _call_llm_sensor(prompt: str, system_prompt: str) -> str | None:
    """Invoke LLM sensor with Gemini as primary and Ollama as resilient fallback."""
    preference = getattr(settings, "LLM_PROVIDER_PREFERENCE", "gemini_first").lower()
    schema = ExtractedTicketFacts.model_json_schema()
    timeout = settings.LLM_FACT_EXTRACTION_TIMEOUT

    if preference == "gemini_first":
        # 1. Primary: Cloud Gemini via LiteLLM
        try:
            raw = await asyncio.wait_for(
                ai_hub.generate_cloud_completion(
                    prompt,
                    system_prompt=system_prompt,
                    temperature=0.0,
                    response_schema=schema,
                ),
                timeout=timeout,
            )
            if raw:
                return raw
        except Exception as exc:
            logger.info("Gemini fact sensor fallback to Ollama: %s", exc)

    # 2. Resilient Fallback: Local Ollama (Qwen)
    if preference in {"gemini_first", "ollama_only"}:
        try:
            return await asyncio.wait_for(
                ai_hub.generate_ollama_completion(
                    prompt,
                    system_prompt=system_prompt,
                    temperature=0.0,
                    response_schema=schema,
                ),
                timeout=timeout,
            )
        except Exception as exc:
            logger.info("Ollama fact sensor abstained: %s", exc)

    return None


async def enrich_task_with_extracted_facts(
    task: dict[str, Any],
    comments_history: list[Any] | None = None,
) -> dict[str, Any]:
    """Return a copy enriched with extracted facts from ticket description and comments."""
    mode = settings.LLM_FACT_EXTRACTION_MODE.lower()
    if mode not in {"shadow", "enabled"}:
        return task

    candidate = extract_person_candidate_from_task(task)
    deterministic_validation = validate_person_candidate(candidate)
    has_explicit_id = _has_explicit_identity(task)

    # Check if there is anything that could benefit from LLM extraction
    base_text = "\n".join(
        str(task.get(key) or "") for key in ("Name", "Description")
    ).strip()
    comments_text, comments_count = _extract_comments_text(task, comments_history)
    full_text = f"{base_text}\n{comments_text}".strip()

    if not full_text:
        return task

    # If person is explicitly invalid from form fields, we never override with LLM
    if has_explicit_id and not deterministic_validation.valid:
        return task

    # If person candidate is valid and we have no comments and explicit identity, skip
    if deterministic_validation.valid and has_explicit_id and not comments_text:
        return task

    task_id = task.get("Id") or task.get("id") or "draft"
    cache_key = _build_content_hash(task_id, full_text)

    # 1. Check Redis Cache
    extracted = await _fetch_from_cache(cache_key)

    if extracted is None:
        prompt = (
            "Ты — сенсор фактов Helpdesk. Извлеки только явно указанные сущности: "
            "ФИО/реквизиты сотрудника (если есть), имя рабочей станции/ПК (например, NTEMW0144), "
            "IP или модель принтера, путь к файлу/папке (SMB), и ответ на уточнение (если заявитель отвечает в комментарии). "
            "Не додумывай. Для каждого извлеченного значения добавь в evidence точную цитату (span).\n\n"
            f"Заявка #{task_id}:\n{full_text}"
        )
        system_prompt = (
            "Ты извлекаешь факты в заданную JSON Schema и воздерживаешься при неоднозначности."
        )

        try:
            raw = await _call_llm_sensor(prompt, system_prompt)
            if raw:
                extracted = ExtractedTicketFacts.model_validate_json(raw)
                extracted.comments_count_analyzed = comments_count
                await _save_to_cache(cache_key, extracted)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.info("LLM fact parsing failed: %s", exc)
        except Exception as exc:
            logger.warning("LLM fact sensor failed closed: %s", exc)

    if extracted is None:
        enriched = deepcopy(task)
        if mode == "enabled":
            enriched["_fact_extraction_manual_review"] = "llm_extraction_unavailable"
        return enriched

    if not _llm_evidence_is_valid(extracted):
        enriched = deepcopy(task)
        enriched["_llm_fact_extraction"] = {
            "mode": mode,
            "accepted": False,
            "reason": "missing_grounded_evidence",
        }
        return enriched

    enriched = deepcopy(task)
    merged = candidate.model_dump()
    for error in deterministic_validation.errors:
        if error.field in merged:
            merged[error.field] = ""

    # Enrich person candidate if extracted and not already provided by explicit field
    if extracted.person:
        for field, value in extracted.person.model_dump().items():
            if not merged.get(field) and value:
                merged[field] = value

    validated = validate_person_candidate(PersonCandidate.model_validate(merged))

    metadata: dict[str, Any] = {
        "mode": mode,
        "accepted": validated.valid if extracted.person else True,
        "ambiguities": extracted.ambiguities,
        "evidence": [item.model_dump() for item in extracted.evidence],
        "comments_count_analyzed": extracted.comments_count_analyzed,
    }
    enriched["_llm_fact_extraction"] = metadata

    if mode == "enabled":
        if extracted.person:
            if validated.valid:
                enriched["_extracted_person"] = merged
            else:
                enriched["_fact_extraction_manual_review"] = (
                    "llm_output_failed_domain_validation"
                )
        if extracted.pc_name:
            norm_pc = normalize_pc_name(extracted.pc_name)
            enriched["_extracted_pc_name"] = norm_pc or extracted.pc_name
        if extracted.printer_address:
            enriched["_extracted_printer_address"] = extracted.printer_address
        if extracted.file_path:
            enriched["_extracted_file_path"] = extracted.file_path
        if extracted.clarification_answer:
            enriched["_extracted_clarification_answer"] = extracted.clarification_answer
        if extracted.issue_summary:
            enriched["_extracted_issue_summary"] = extracted.issue_summary

    return enriched
