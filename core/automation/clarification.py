"""Idempotent clarification and conservative fact merging for AD onboarding."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.contracts import SnapshotComment
from core.automation.llm_transport import LLMTransport
from core.automation.workflows import WorkflowFactSpec
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


@dataclass
class FactCorrectionProposal:
    param_key: str
    old_value: str
    new_value: str
    text_span: str
    comment_id: int | None = None
    reason: str = ""


def clarification_fingerprint(task_id: int, snapshot_hash: str, missing_facts: Iterable[str]) -> str:
    payload = {
        "task_id": task_id,
        "snapshot_hash": snapshot_hash,
        "missing_facts": sorted(set(missing_facts)),
        "template_version": TEMPLATE_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def render_clarification(
    missing_facts: Iterable[str],
    fact_specs: Iterable[WorkflowFactSpec] | None = None,
) -> str:
    missing = list(dict.fromkeys(missing_facts))
    if "last_name" in missing and "first_name" in missing:
        return "Укажите, пожалуйста, полные ФИО сотрудника, для которого необходимо создать учётную запись."
    specs_map = {f.key: f.label for f in (fact_specs or ())}
    labels = [specs_map.get(key) or FACT_LABELS.get(key, key) for key in missing]
    return "Для продолжения решения заявки укажите, пожалуйста: " + ", ".join(labels) + "."


_NON_NAME_TOKENS = {
    "спасибо",
    "большое",
    "добрый",
    "день",
    "вечер",
    "утро",
    "здравствуйте",
    "привет",
    "приветствую",
    "хорошо",
    "ок",
    "ладно",
    "принято",
    "понятно",
    "ясно",
    "сделано",
    "отмените",
    "отмена",
    "не",
    "надо",
    "нужно",
    "жду",
    "пожалуйста",
    "благодарю",
    "прошу",
    "здравствуй",
    "hi",
    "hello",
    "thanks",
    "thank",
    "you",
    "ok",
    "yes",
    "no",
}


def extract_labeled_facts(
    text: str,
    allowed_facts: Iterable[str] | None = None,
    *,
    fact_specs: Iterable[WorkflowFactSpec] | None = None,
) -> dict[str, dict[str, str]]:
    """Extract only literal labelled spans; never infer an unstated value."""
    specs_map: dict[str, WorkflowFactSpec] = {}
    if fact_specs is not None:
        specs_map = {f.key: f for f in fact_specs}
        allowed = set(specs_map.keys())
        if allowed_facts is not None:
            allowed &= set(allowed_facts)
    else:
        allowed = set(allowed_facts or ()) & set(ONBOARDING_FACTS)

    patterns_to_check: dict[str, tuple[str, ...]] = {}
    for key in allowed:
        if key in specs_map:
            spec = specs_map[key]
            patterns_to_check[key] = (spec.label, *spec.synonyms)
        elif key in _LABEL_PATTERNS:
            patterns_to_check[key] = _LABEL_PATTERNS[key]

    result: dict[str, dict[str, str]] = {}
    for raw_line in re.split(r"[\r\n;]+", text):
        line = raw_line.strip(" \t-*•")
        if not line:
            continue
        for key, labels in patterns_to_check.items():
            if key not in allowed or key in result:
                continue
            pattern = rf"(?:^|.*?[;,\n:]\s*)(?:{'|'.join(re.escape(label) for label in labels)})\s*[:\-–—]\s*(.+?)\s*$"
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if value:
                    spec = specs_map.get(key)
                    canonical = spec.normalize(value) if spec is not None else value
                    result[key] = {"value": canonical, "text_span": match.group(0)}
                break

    # The dedicated first-round FIO template accepts a literal three/two-part reply.
    if {"last_name", "first_name"}.issubset(allowed) and not ({"last_name", "first_name"} & result.keys()):
        compact = " ".join(text.split())
        if re.fullmatch(r"[A-Za-zА-Яа-яЁё-]+(?:\s+[A-Za-zА-Яа-яЁё-]+){1,2}", compact):
            parts = compact.split()
            if not any(part.casefold() in _NON_NAME_TOKENS for part in parts):
                result["last_name"] = {"value": parts[0].capitalize(), "text_span": compact}
                result["first_name"] = {"value": parts[1].capitalize(), "text_span": compact}
                if len(parts) == 3 and "middle_name" in allowed:
                    result["middle_name"] = {"value": parts[2].capitalize(), "text_span": compact}
    return result



def merge_case_facts(
    structured: dict[str, str],
    responses: Iterable[dict[str, Any]],
    allowed_keys: Iterable[str] | None = None,
) -> FactMergeResult:
    merged = FactMergeResult()
    allowed_set = set(allowed_keys) if allowed_keys is not None else None

    def add(key: str, value: Any, source: dict[str, Any]) -> None:
        clean = " ".join(str(value or "").split())
        if not clean:
            return
        if allowed_set is not None and key not in allowed_set:
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

    init_keys = allowed_set if allowed_set is not None else list(structured.keys())
    for key in init_keys:
        if key in structured:
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


def merge_onboarding_facts(
    structured: dict[str, str],
    responses: Iterable[dict[str, Any]],
) -> FactMergeResult:
    return merge_case_facts(structured, responses, allowed_keys=ONBOARDING_FACTS)


def latest_human_public_event(
    events: Iterable[TaskLifetimeEventDTO],
    *,
    baseline_event_id: int | None,
    baseline_event_key: str | None = None,
    bot_user_id: int,
    applicant_id: int | None = None,
    question_text: str | None = None,
) -> TaskLifetimeEventDTO | None:
    ordered = list(events)
    norm_question = " ".join((question_text or "").split()).strip()
    boundary_index: int | None = None

    # Prefer the stable key for newly-created records. Retain the legacy ID
    # lookup so already-persisted clarification rows remain readable.
    if baseline_event_key:
        matches = [index for index, event in enumerate(ordered) if event.event_key == baseline_event_key]
        if matches:
            boundary_index = matches[-1]
    elif baseline_event_id is not None:
        matches = [index for index, event in enumerate(ordered) if event.id == baseline_event_id]
        if matches:
            boundary_index = matches[-1]

    # The published question is a stronger boundary than a legacy positional
    # ID. This safely reconciles records created before stable event keys.
    question_indexes = [
        index
        for index, event in enumerate(ordered)
        if norm_question and not event.is_private and " ".join((event.comment or "").split()).strip() == norm_question
    ]
    if question_indexes:
        question_boundary = question_indexes[-1]
        boundary_index = max(boundary_index if boundary_index is not None else -1, question_boundary)

    # A persisted boundary that cannot be located must fail closed. Otherwise
    # an old comment could be mistaken for a new response.
    if boundary_index is None and (baseline_event_key or baseline_event_id is not None):
        return None

    candidates = []
    for index, event in enumerate(ordered):
        if boundary_index is not None and index <= boundary_index:
            continue
        if event.id is None:
            continue
        if event.is_private:
            continue
        comment = (event.comment or "").strip()
        if not comment:
            continue
        if event.editor_id is not None and event.editor_id == bot_user_id:
            continue
        if applicant_id is None or event.editor_id != applicant_id:
            continue
        # Guard against bot's own question being mistaken for a human reply
        if norm_question and " ".join(comment.split()).strip() == norm_question:
            continue
        candidates.append(event)

    return candidates[-1] if candidates else None


class UniversalIntentExtractor:
    """Bounded LLM intent and parameter extractor with strict groundedness."""

    def __init__(self, transport: LLMTransport, model_alias: str = "helpdesk-fast") -> None:
        self.transport = transport
        self.model_alias = model_alias
        self.last_diagnostic: dict[str, Any] = {}

    async def extract_with_trace(
        self,
        text: str,
        missing_facts: Iterable[str],
        *,
        fact_specs: Iterable[WorkflowFactSpec] | None = None,
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        specs_list = list(fact_specs) if fact_specs is not None else []
        specs_map = {f.key: f for f in specs_list}
        if specs_map:
            allowed = sorted(set(missing_facts) & set(specs_map.keys()))
        else:
            allowed = sorted(set(missing_facts) & set(ONBOARDING_FACTS))

        deterministic = extract_labeled_facts(text, allowed, fact_specs=fact_specs)
        remaining = [key for key in allowed if key not in deterministic]
        diag: dict[str, Any] = {
            "model_alias": self.model_alias,
            "requested_keys": allowed,
            "deterministic_keys": list(deterministic.keys()),
            "llm_attempted": False,
            "llm_used": False,
            "llm_keys": [],
            "error": None,
        }
        if not remaining:
            self.last_diagnostic = diag
            return deterministic, diag

        diag["llm_attempted"] = True
        sanitized = _SECRET_RE.sub("[SECRET]", text)[:4000]
        try:
            raw = await self.transport.complete_json(
                model_alias=self.model_alias,
                system_prompt=(
                    "Extract only explicitly stated parameters for allowed_keys. "
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
            llm_added: list[str] = []
            for item in items:
                if not isinstance(item, dict) or set(item) != {"key", "value", "text_span"}:
                    raise ValueError("invalid_clarification_fact_schema")
                key = str(item["key"])
                raw_val = " ".join(str(item["value"]).split())
                span = str(item["text_span"])
                # Groundedness checks: span in sanitized, case-insensitive value in span
                if key not in remaining or not raw_val or not span or span not in sanitized or raw_val.casefold() not in span.casefold():
                    raise ValueError("ungrounded_clarification_fact")
                spec = specs_map.get(key)
                canonical = spec.normalize(raw_val) if spec is not None else raw_val
                if spec is not None and spec.pattern and not re.match(spec.pattern, canonical):
                    raise ValueError("invalid_fact_pattern")
                result[key] = {"value": canonical, "text_span": span}
                llm_added.append(key)
            if llm_added:
                diag["llm_used"] = True
                diag["llm_keys"] = llm_added
            self.last_diagnostic = diag
            return result, diag
        except Exception as exc:
            diag["error"] = type(exc).__name__
            self.last_diagnostic = diag
            return deterministic, diag

    async def extract(
        self,
        text: str,
        missing_facts: Iterable[str],
        *,
        fact_specs: Iterable[WorkflowFactSpec] | None = None,
    ) -> dict[str, dict[str, str]]:
        facts, _ = await self.extract_with_trace(text, missing_facts, fact_specs=fact_specs)
        return facts

    async def detect_corrections_with_trace(
        self,
        comments: list[SnapshotComment] | list[Any],
        current_values: dict[str, str],
        fact_specs: Iterable[WorkflowFactSpec],
        *,
        applicant_id: int | None = None,
        bot_user_id: int | None = None,
    ) -> tuple[list[FactCorrectionProposal], dict[str, Any]]:
        diag: dict[str, Any] = {
            "model_alias": self.model_alias,
            "deterministic_proposals": [],
            "llm_proposals": [],
            "llm_attempted": False,
            "llm_used": False,
            "error": None,
        }
        specs_list = list(fact_specs)
        specs_by_key = {
            f.key: f for f in specs_list if f.key in current_values and str(current_values[f.key]).strip()
        }
        if not specs_by_key:
            return [], diag

        eligible_comments = []
        for c in comments:
            is_priv = getattr(c, "is_private", False)
            if is_priv:
                continue
            ed_id = getattr(c, "editor_id", None)
            if bot_user_id is not None and ed_id == bot_user_id:
                continue
            if applicant_id is not None and ed_id != applicant_id:
                continue
            text = (getattr(c, "text", "") or getattr(c, "comment", "") or "").strip()
            if text:
                eligible_comments.append(c)

        if not eligible_comments:
            return [], diag

        proposals: list[FactCorrectionProposal] = []
        seen_keys: set[str] = set()

        # 1. Deterministic check on each eligible comment
        for comment in reversed(eligible_comments):
            c_text = getattr(comment, "text", "") or getattr(comment, "comment", "") or ""
            c_id = getattr(comment, "id", None)
            labeled = extract_labeled_facts(c_text, fact_specs=specs_list)
            for k, item in labeled.items():
                if k in specs_by_key and k not in seen_keys:
                    new_val = item["value"]
                    old_val = current_values[k]
                    if new_val.casefold() != old_val.casefold():
                        prop = FactCorrectionProposal(
                            param_key=k,
                            old_value=old_val,
                            new_value=new_val,
                            text_span=item["text_span"],
                            comment_id=c_id,
                            reason="deterministic_labeled_correction",
                        )
                        proposals.append(prop)
                        seen_keys.add(k)
                        diag["deterministic_proposals"].append(k)

        # 2. LLM Intent & Correction Extraction for uncorrected keys
        remaining_specs = [s for k, s in specs_by_key.items() if k not in seen_keys]
        if remaining_specs and eligible_comments:
            recent_texts = []
            for c in eligible_comments[-3:]:
                t = (getattr(c, "text", "") or getattr(c, "comment", "") or "").strip()
                if t:
                    recent_texts.append(t)
            combined_text = "\n---\n".join(recent_texts)
            sanitized = _SECRET_RE.sub("[SECRET]", combined_text)[:4000]

            has_substance = any(len(t) > 3 for t in recent_texts)
            if has_substance:
                diag["llm_attempted"] = True
                summary = {
                    s.key: {
                        "label": s.label,
                        "current_value": current_values[s.key],
                    }
                    for s in remaining_specs
                }
                try:
                    raw = await self.transport.complete_json(
                        model_alias=self.model_alias,
                        system_prompt=(
                            "You are a helpdesk parameter correction detector. "
                            "Analyze user comments against current ticket parameters. "
                            "Determine if the user explicitly corrects or updates an existing parameter (e.g., fixing a typo, updating location, changing value, specifying correct full name). "
                            "DO NOT treat mentions of third parties (managers, colleagues, 'agreed with Ivanov') as corrections. "
                            "Return JSON {corrections:[{param_key,old_value,new_value,text_span,reason}]}. "
                            "text_span must be an exact literal quote from source_text where the correction was stated. "
                            "new_value must appear inside text_span. "
                            "If no parameter is being corrected, return {corrections:[]}."
                        ),
                        payload={"current_parameters": summary, "source_text": sanitized},
                        timeout_seconds=15.0,
                    )
                    parsed = json.loads(raw)
                    items = parsed.get("corrections", [])
                    if isinstance(items, list):
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            pkey = str(item.get("param_key", ""))
                            raw_new = str(item.get("new_value", "")).strip()
                            span = str(item.get("text_span", "")).strip()
                            reason = str(item.get("reason", "applicant_stated_correction"))
                            if pkey not in specs_by_key or pkey in seen_keys or not raw_new or not span:
                                continue
                            # Strict Groundedness check
                            if span not in sanitized or raw_new.casefold() not in span.casefold():
                                continue
                            spec = specs_by_key[pkey]
                            canonical = spec.normalize(raw_new)
                            old_val = current_values[pkey]
                            if canonical.casefold() == old_val.casefold():
                                continue
                            if spec.pattern and not re.match(spec.pattern, canonical):
                                continue
                            # Find comment_id corresponding to this span
                            matched_comment_id = None
                            for c in reversed(eligible_comments):
                                c_text = getattr(c, "text", "") or getattr(c, "comment", "") or ""
                                if span in c_text:
                                    matched_comment_id = getattr(c, "id", None)
                                    break
                            prop = FactCorrectionProposal(
                                param_key=pkey,
                                old_value=old_val,
                                new_value=canonical,
                                text_span=span,
                                comment_id=matched_comment_id,
                                reason=reason,
                            )
                            proposals.append(prop)
                            seen_keys.add(pkey)
                            diag["llm_proposals"].append(pkey)
                        if diag["llm_proposals"]:
                            diag["llm_used"] = True
                except Exception as exc:
                    diag["error"] = type(exc).__name__

        return proposals, diag


ClarificationFactExtractor = UniversalIntentExtractor



class ClarificationConvergenceService:
    """Manages idempotent, generation-aware clarification convergence.

    Ensures that active clarification requests resolve when required facts converge,
    without resurrecting 'zombie' missing facts from past rounds.
    """

    async def converge(
        self,
        session: AsyncSession,
        *,
        task_id: int,
        missing_facts: Iterable[str],
        snapshot_hash: str,
        current_generation: int = 1,
    ) -> Any | None:
        from core.database.models import ClarificationRequestRecord

        active = await session.scalar(
            select(ClarificationRequestRecord)
            .where(
                ClarificationRequestRecord.task_id == task_id,
                ClarificationRequestRecord.state.in_(["pending_publish", "published"]),
            )
            .order_by(ClarificationRequestRecord.created_at.desc())
            .limit(1)
        )
        if active is None:
            return None

        # Missing facts truth source is strictly the caller's active evaluation
        active_missing = sorted(set(missing_facts))

        if not active_missing:
            # All facts converged. Execute generation-aware CAS update.
            stmt = (
                update(ClarificationRequestRecord)
                .where(
                    ClarificationRequestRecord.id == active.id,
                    ClarificationRequestRecord.state.in_(["pending_publish", "published"]),
                    ClarificationRequestRecord.generation == current_generation,
                )
                .values(
                    state="resolved",
                    resolution_source="facts_converged",
                    resolved_snapshot_hash=snapshot_hash,
                    resolved_at=datetime.now(UTC),
                )
            )
            res = await session.execute(stmt)
            if res.rowcount > 0:
                active.state = "resolved"
                active.resolution_source = "facts_converged"
                active.resolved_snapshot_hash = snapshot_hash
                active.resolved_at = datetime.now(UTC)
            return active

        # Facts still missing: update missing_facts_json to reflect strictly current state
        if list(active.missing_facts_json or []) != active_missing:
            stmt = (
                update(ClarificationRequestRecord)
                .where(
                    ClarificationRequestRecord.id == active.id,
                    ClarificationRequestRecord.state.in_(["pending_publish", "published"]),
                    ClarificationRequestRecord.generation == current_generation,
                )
                .values(
                    missing_facts_json=active_missing,
                )
            )
            await session.execute(stmt)
            active.missing_facts_json = active_missing

        return active
