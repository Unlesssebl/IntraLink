"""Service-aware routing contracts and deterministic resolvers.

Service identifiers are accepted only from a synchronized IntraService catalog.
Neither ticket text nor an LLM response can manufacture or authorize an ID.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Iterable
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from core.automation.case_profiles import CaseProfileRegistry
from core.automation.contracts import CaseDecision, FrozenModel, TicketSnapshot
from core.automation.llm_transport import LLMTransport, LLMTransportError
from core.rag.sanitizer import PIISanitizer

_SECRET_RE = re.compile(r"(?i)\b(password|passwd|пароль|token|secret)\b\s*[:=]?\s*\S+")


class CatalogValidationState(str, Enum):
    pending = "pending"
    validated = "validated"
    invalid = "invalid"


class ServiceCompatibilityState(str, Enum):
    compatible = "compatible"
    mismatch = "mismatch"
    ambiguous = "ambiguous"
    unknown = "unknown"
    degraded = "degraded"


class RedirectStrategy(str, Enum):
    cancel_and_recreate = "cancel_and_recreate"
    transfer_service = "transfer_service"
    manual = "manual"


class RedirectPlanState(str, Enum):
    ready = "ready"
    approved = "approved"
    executing = "executing"
    succeeded = "succeeded"
    rejected = "rejected"
    manual = "manual"
    needs_review = "needs_review"


class TargetSelectionState(str, Enum):
    source_match = "source_match"
    target_suggested = "target_suggested"
    ambiguous = "ambiguous"
    not_found = "not_found"
    unavailable = "unavailable"


class ServiceCatalogEntry(FrozenModel):
    service_id: int
    service_path: str
    parent_service_id: int | None = None
    task_type_id: int | None = None
    is_active: bool
    form_metadata: dict[str, Any] = Field(default_factory=dict)
    field_metadata: list[dict[str, Any]] = Field(default_factory=list)
    catalog_hash: str = ""


class ServiceCatalogVersion(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    version: int = Field(ge=1)
    catalog_hash: str = Field(min_length=64, max_length=64)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str
    validation_state: CatalogValidationState
    is_active: bool = False


class ServiceRouteBinding(FrozenModel):
    key: str
    version: str
    service_ids: tuple[int, ...]
    allowed_case_types: tuple[str, ...]
    default_case_type: str | None = None
    allowed_workflows: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    required_task_type_id: int | None = None
    required_fields: tuple[str, ...] = ()
    redirect_strategy: RedirectStrategy = RedirectStrategy.manual
    risk: str = "medium"
    is_active: bool = False
    is_validated: bool = False
    catalog_hash: str


class RedirectCandidate(FrozenModel):
    service_id: int
    service_path: str
    binding_key: str
    binding_version: str
    redirect_strategy: RedirectStrategy
    evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    verifier_result: str | None = None


class ServiceTargetCandidate(FrozenModel):
    """Advisory catalog target selected independently from execution authorization."""

    service_id: int
    service_path: str
    confidence: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    score_components: dict[str, float] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class TargetServiceResolution(FrozenModel):
    """Catalog-grounded routing result produced before case classification."""

    task_id: int
    snapshot_hash: str = Field(min_length=64, max_length=64)
    source_service_id: int | None = None
    source_service_path: str | None = None
    catalog_hash: str | None = None
    catalog_state: str = "unavailable"
    state: TargetSelectionState
    selected_service_id: int | None = None
    selected_service_path: str | None = None
    method: str = "catalog_first"
    candidates: list[ServiceTargetCandidate] = Field(default_factory=list, max_length=10)
    evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    llm_used: bool = False
    llm_verdict: str | None = None
    llm_reason: str | None = None
    llm_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_resolution(self) -> "TargetServiceResolution":
        selected_states = {TargetSelectionState.source_match, TargetSelectionState.target_suggested}
        if self.state in selected_states and self.selected_service_id is None:
            raise ValueError("selected target state requires a service")
        if self.state not in selected_states and self.selected_service_id is not None:
            raise ValueError("non-selected target state cannot expose a selected service")
        return self


class ServiceCompatibilityDecision(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: int
    snapshot_hash: str = Field(min_length=64, max_length=64)
    case_decision_id: UUID
    source_service_id: int | None = None
    source_service_path: str | None = None
    source_task_type_id: int | None = None
    binding_key: str | None = None
    binding_version: str | None = None
    catalog_hash: str | None = None
    allowed_case_types: list[str] = Field(default_factory=list)
    target_service_id: int | None = None
    target_service_path: str | None = None
    target_selection_state: TargetSelectionState = TargetSelectionState.unavailable
    target_selection_method: str | None = None
    target_candidates: list[ServiceTargetCandidate] = Field(default_factory=list, max_length=10)
    routing_evidence: list[str] = Field(default_factory=list)
    routing_contradictions: list[str] = Field(default_factory=list)
    catalog_state: str = "unavailable"
    authorization_state: str = "unavailable"
    analysis_timings_ms: dict[str, int] = Field(default_factory=dict)
    llm_used: bool = False
    target_reranker_verdict: str | None = None
    target_reranker_reason: str | None = None
    target_reranker_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    candidates: list[RedirectCandidate] = Field(default_factory=list, max_length=3)
    evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    degraded_component: str | None = None
    state: ServiceCompatibilityState
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_state(self) -> "ServiceCompatibilityDecision":
        if self.state == ServiceCompatibilityState.mismatch and len(self.candidates) != 1:
            raise ValueError("mismatch requires exactly one target candidate")
        if self.state == ServiceCompatibilityState.ambiguous and len(self.candidates) < 2:
            raise ValueError("ambiguous requires multiple target candidates")
        if self.state == ServiceCompatibilityState.degraded and not self.degraded_component:
            raise ValueError("degraded requires degraded_component")
        return self


class RedirectPlan(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    task_id: int
    snapshot_hash: str = Field(min_length=64, max_length=64)
    case_decision_id: UUID
    compatibility_decision_id: UUID
    source_service_id: int
    target_service_id: int
    target_service_path: str
    strategy: RedirectStrategy
    template_key: str
    rendered_public_comment: str
    catalog_hash: str = Field(min_length=64, max_length=64)
    binding_key: str
    binding_version: str
    plan_hash: str = ""
    state: RedirectPlanState = RedirectPlanState.ready
    approval_state: str = "pending"
    execution_state: str = "not_started"
    execution_steps: list[dict[str, Any]] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_hash(self) -> "RedirectPlan":
        expected = compute_redirect_plan_hash(self)
        if self.plan_hash and self.plan_hash != expected:
            raise ValueError("RedirectPlan plan_hash is not canonical")
        return self


def canonical_catalog_hash(entries: Iterable[ServiceCatalogEntry | dict[str, Any]]) -> str:
    canonical: list[dict[str, Any]] = []
    for raw in entries:
        data = raw.model_dump(mode="json") if isinstance(raw, ServiceCatalogEntry) else dict(raw)
        data.pop("catalog_hash", None)
        canonical.append(
            {
                "service_id": int(data["service_id"]),
                "service_path": str(data["service_path"]).strip(),
                "parent_service_id": data.get("parent_service_id"),
                "task_type_id": data.get("task_type_id"),
                "is_active": bool(data.get("is_active", False)),
                "form_metadata": data.get("form_metadata") or {},
                "field_metadata": data.get("field_metadata") or [],
            }
        )
    canonical.sort(key=lambda item: item["service_id"])
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compute_redirect_plan_hash(plan: RedirectPlan | dict[str, Any]) -> str:
    data = plan.model_dump(mode="json") if isinstance(plan, RedirectPlan) else dict(plan)
    canonical = {
        key: data.get(key)
        for key in (
            "id",
            "task_id",
            "snapshot_hash",
            "case_decision_id",
            "compatibility_decision_id",
            "source_service_id",
            "target_service_id",
            "target_service_path",
            "strategy",
            "template_key",
            "rendered_public_comment",
            "catalog_hash",
            "binding_key",
            "binding_version",
        )
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RedirectResolver:
    """Resolves a target first, then evaluates execution authorization."""

    _TOKEN_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)
    _STOP_WORDS = frozenset(
        {
            "для",
            "или",
            "при",
            "под",
            "над",
            "вопросы",
            "общие",
            "проблемы",
            "обслуживание",
            "системы",
            "система",
        }
    )

    def resolve_target(
        self,
        *,
        snapshot: TicketSnapshot,
        catalog_hash: str | None,
        entries: Iterable[ServiceCatalogEntry],
        catalog_fetched_at: datetime | None = None,
    ) -> TargetServiceResolution:
        """Choose an advisory catalog target without case types or bindings."""
        if not catalog_hash:
            return TargetServiceResolution(
                task_id=snapshot.task_id,
                snapshot_hash=snapshot.snapshot_hash,
                source_service_id=snapshot.service_id,
                catalog_hash=None,
                catalog_state="unavailable",
                state=TargetSelectionState.unavailable,
                reason_codes=["catalog_unavailable"],
            )

        active = {
            entry.service_id: entry for entry in entries if entry.is_active and entry.catalog_hash == catalog_hash
        }
        source = active.get(snapshot.service_id) if snapshot.service_id is not None else None
        if not active:
            return TargetServiceResolution(
                task_id=snapshot.task_id,
                snapshot_hash=snapshot.snapshot_hash,
                source_service_id=snapshot.service_id,
                catalog_hash=catalog_hash,
                catalog_state="unavailable",
                state=TargetSelectionState.unavailable,
                reason_codes=["catalog_has_no_active_entries"],
            )

        parent_ids = {entry.parent_service_id for entry in active.values() if entry.parent_service_id is not None}
        leaves = [entry for entry in active.values() if entry.service_id not in parent_ids]
        text = " ".join(
            [snapshot.title, snapshot.description]
            + [item.text for item in snapshot.public_comments if not item.is_private]
        )
        normalized_text = self._normalize(text)
        text_tokens = set(self._tokens(normalized_text))

        ranked: list[tuple[float, ServiceTargetCandidate]] = []
        for entry in leaves:
            evidence: list[str] = []
            components: dict[str, float] = {}
            is_source = source is not None and entry.service_id == source.service_id
            is_source_descendant = (
                source is not None
                and source.service_id in parent_ids
                and self._is_descendant(entry, source, active)
            )
            if is_source:
                evidence.extend(["source_service_active", "active_catalog_leaf"])

            leaf_name = self._normalize(entry.service_path.split("→")[-1])
            leaf_tokens = set(self._tokens(leaf_name))
            overlap = sorted(leaf_tokens & text_tokens)
            if leaf_name and len(leaf_name) >= 5 and leaf_name in normalized_text:
                components["exact_leaf_name"] = 0.70
                evidence.append("ticket_exact_service_name")
            if leaf_tokens:
                token_coverage = len(overlap) / len(leaf_tokens)
                if token_coverage:
                    components["term_coverage"] = round(0.45 * token_coverage, 4)
                evidence.extend(f"ticket_service_term:{token}" for token in overlap)

                fuzzy_matches = 0
                fuzzy_threshold = 0.73 if is_source else 0.82
                for service_token in leaf_tokens - set(overlap):
                    if len(service_token) < 6:
                        continue
                    best_ratio = max(
                        (
                            SequenceMatcher(None, service_token, ticket_token).ratio()
                            for ticket_token in text_tokens
                            if len(ticket_token) >= 6
                        ),
                        default=0.0,
                    )
                    if best_ratio >= fuzzy_threshold:
                        fuzzy_matches += 1
                fuzzy_is_grounded = fuzzy_matches >= 2 or bool(overlap) or is_source
                if fuzzy_matches and fuzzy_is_grounded:
                    components["fuzzy_term_coverage"] = round(0.25 * fuzzy_matches / len(leaf_tokens), 4)
                    evidence.append("ticket_fuzzy_service_terms")

            if is_source_descendant:
                evidence.append("source_service_descendant")

            score = min(1.0, round(sum(components.values()), 4))
            if score == 0.0 and not is_source and not is_source_descendant:
                continue
            ranked.append(
                (
                    score,
                    ServiceTargetCandidate(
                        service_id=entry.service_id,
                        service_path=entry.service_path,
                        confidence="high" if score >= 0.75 else "medium" if score >= 0.35 else "low",
                        score=score,
                        score_components=components,
                        evidence=evidence,
                    ),
                )
            )

        ranked.sort(
            key=lambda item: (
                -item[0],
                item[1].service_id != snapshot.service_id,
                item[1].service_path,
                item[1].service_id,
            )
        )
        candidates = [item[1] for item in ranked[:10]]
        if source is not None and source.service_id not in {item.service_id for item in candidates}:
            source_item = next((item for _, item in ranked if item.service_id == source.service_id), None)
            if source_item is not None:
                candidates = (candidates[:9] + [source_item]) if len(candidates) >= 10 else candidates + [source_item]
        selected: ServiceTargetCandidate | None = None
        state = TargetSelectionState.not_found
        reason_codes = ["target_service_not_found"]

        if ranked:
            top_score, top = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else 0
            has_text_evidence = bool(top.score_components)
            strong_match = top_score >= 0.75 and top_score - second_score >= 0.15
            supported_source = (
                source is not None
                and top.service_id == source.service_id
                and has_text_evidence
                and top_score >= 0.10
            )
            if strong_match or supported_source:
                selected = top
                if source is not None and top.service_id == source.service_id:
                    state = TargetSelectionState.source_match
                    reason_codes = ["source_service_supported_by_ticket_text"]
                else:
                    state = TargetSelectionState.target_suggested
                    reason_codes = ["ticket_text_identifies_catalog_target"]
            else:
                state = TargetSelectionState.ambiguous
                reason_codes = ["catalog_target_requires_llm_rerank"]

        if source is None and snapshot.service_id is not None:
            reason_codes.append("source_service_unknown_or_inactive")

        return TargetServiceResolution(
            task_id=snapshot.task_id,
            snapshot_hash=snapshot.snapshot_hash,
            source_service_id=snapshot.service_id,
            source_service_path=source.service_path if source else None,
            catalog_hash=catalog_hash,
            catalog_state=self._catalog_state(catalog_fetched_at),
            state=state,
            selected_service_id=selected.service_id if selected else None,
            selected_service_path=selected.service_path if selected else None,
            method="catalog_top10",
            candidates=candidates,
            evidence=list(selected.evidence) if selected else [],
            reason_codes=reason_codes,
        )

    def resolve(
        self,
        *,
        decision: CaseDecision,
        source_service_id: int | None,
        source_task_type_id: int | None,
        catalog_hash: str | None,
        entries: Iterable[ServiceCatalogEntry],
        bindings: Iterable[ServiceRouteBinding],
        target_resolution: TargetServiceResolution | None = None,
    ) -> ServiceCompatibilityDecision:
        case_type = decision.primary_case_type
        entries = list(entries)
        if not catalog_hash:
            return self._degraded(decision, source_service_id, "service_catalog", "catalog_unavailable")
        active_entries = {
            entry.service_id: entry for entry in entries if entry.is_active and entry.catalog_hash == catalog_hash
        }
        source = active_entries.get(source_service_id) if source_service_id is not None else None
        if target_resolution is None:
            target = self._select_advisory_target(case_type, source, active_entries.values())
        else:
            target = {
                "target_service_id": target_resolution.selected_service_id,
                "target_service_path": target_resolution.selected_service_path,
                "target_selection_state": target_resolution.state,
                "target_selection_method": target_resolution.method,
                "target_candidates": target_resolution.candidates,
                "catalog_state": target_resolution.catalog_state,
                "routing_evidence": list(target_resolution.evidence),
                "routing_contradictions": list(target_resolution.contradictions),
                "target_reranker_verdict": target_resolution.llm_verdict,
                "target_reranker_reason": target_resolution.llm_reason,
                "target_reranker_confidence": target_resolution.llm_confidence,
            }
        active_bindings = [b for b in bindings if b.is_active and b.is_validated and b.catalog_hash == catalog_hash]
        if not active_bindings:
            return ServiceCompatibilityDecision(
                task_id=decision.task_id,
                snapshot_hash=decision.snapshot_hash,
                case_decision_id=decision.id,
                source_service_id=source_service_id,
                source_service_path=source.service_path if source else None,
                source_task_type_id=source_task_type_id,
                catalog_hash=catalog_hash,
                state=ServiceCompatibilityState.degraded,
                degraded_component="service_binding",
                reason_codes=["binding_unavailable"],
                authorization_state="binding_unavailable",
                **target,
            )
        if source is None:
            return ServiceCompatibilityDecision(
                task_id=decision.task_id,
                snapshot_hash=decision.snapshot_hash,
                case_decision_id=decision.id,
                source_service_id=source_service_id,
                catalog_hash=catalog_hash,
                state=ServiceCompatibilityState.unknown,
                reason_codes=["source_service_unknown_or_inactive"],
                authorization_state="source_service_unavailable",
                **target,
            )
        source_binding = next((b for b in active_bindings if source.service_id in b.service_ids), None)
        if source_binding and case_type in source_binding.allowed_case_types:
            task_type_ok = (
                source_binding.required_task_type_id is None
                or source_task_type_id == source_binding.required_task_type_id
            )
            if not task_type_ok:
                return ServiceCompatibilityDecision(
                    task_id=decision.task_id,
                    snapshot_hash=decision.snapshot_hash,
                    case_decision_id=decision.id,
                    source_service_id=source.service_id,
                    source_service_path=source.service_path,
                    source_task_type_id=source_task_type_id,
                    binding_key=source_binding.key,
                    binding_version=source_binding.version,
                    catalog_hash=catalog_hash,
                    allowed_case_types=list(source_binding.allowed_case_types),
                    state=ServiceCompatibilityState.unknown,
                    contradictions=["task_type_mismatch"],
                    reason_codes=["task_type_not_authorized"],
                    authorization_state="task_type_not_authorized",
                    **target,
                )
            return ServiceCompatibilityDecision(
                task_id=decision.task_id,
                snapshot_hash=decision.snapshot_hash,
                case_decision_id=decision.id,
                source_service_id=source.service_id,
                source_service_path=source.service_path,
                source_task_type_id=source_task_type_id,
                binding_key=source_binding.key,
                binding_version=source_binding.version,
                catalog_hash=catalog_hash,
                allowed_case_types=list(source_binding.allowed_case_types),
                state=ServiceCompatibilityState.compatible,
                evidence=["source_service_matches_validated_binding", "case_type_allowed"],
                reason_codes=["service_compatible"],
                authorization_state="authorized",
                **target,
            )
        candidates: list[RedirectCandidate] = []
        for binding in active_bindings:
            if case_type not in binding.allowed_case_types:
                continue
            for service_id in binding.service_ids:
                entry = active_entries.get(service_id)
                if entry is None:
                    continue
                contradictions: list[str] = []
                if binding.required_task_type_id is not None and entry.task_type_id != binding.required_task_type_id:
                    contradictions.append("catalog_task_type_mismatch")
                required = set(binding.required_fields)
                available = {
                    str(field.get("key") or field.get("id") or field.get("Id")) for field in entry.field_metadata
                }
                if required and not required.issubset(available):
                    contradictions.append("required_form_fields_missing")
                if contradictions:
                    continue
                candidates.append(
                    RedirectCandidate(
                        service_id=entry.service_id,
                        service_path=entry.service_path,
                        binding_key=binding.key,
                        binding_version=binding.version,
                        redirect_strategy=binding.redirect_strategy,
                        evidence=["active_catalog_entry", "case_type_allowed", "binding_validated"],
                    )
                )
        candidates = sorted(candidates, key=lambda item: (item.service_path, item.service_id))[:3]
        if len(candidates) == 1:
            state, reasons = ServiceCompatibilityState.mismatch, ["single_valid_target"]
        elif len(candidates) > 1:
            state, reasons = ServiceCompatibilityState.ambiguous, ["multiple_valid_targets"]
        else:
            state, reasons = ServiceCompatibilityState.unknown, ["target_service_not_found"]
        return ServiceCompatibilityDecision(
            task_id=decision.task_id,
            snapshot_hash=decision.snapshot_hash,
            case_decision_id=decision.id,
            source_service_id=source.service_id,
            source_service_path=source.service_path,
            source_task_type_id=source_task_type_id,
            catalog_hash=catalog_hash,
            candidates=candidates,
            state=state,
            reason_codes=reasons,
            authorization_state="not_authorized",
            **target,
        )

    @classmethod
    def _tokens(cls, value: str) -> list[str]:
        return [
            token
            for token in cls._TOKEN_RE.findall(value)
            if len(token) >= 3 and token not in cls._STOP_WORDS and not token.isdigit()
        ]

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().replace("ё", "е").replace("wi-fi", "wifi").split())

    @staticmethod
    def _catalog_state(fetched_at: datetime | None) -> str:
        if fetched_at is None:
            return "available"
        normalized = fetched_at if fetched_at.tzinfo is not None else fetched_at.replace(tzinfo=UTC)
        return "stale" if datetime.now(UTC) - normalized > timedelta(hours=24) else "available"

    @staticmethod
    def _is_descendant(
        entry: ServiceCatalogEntry,
        ancestor: ServiceCatalogEntry,
        entries: dict[int, ServiceCatalogEntry],
    ) -> bool:
        parent_id = entry.parent_service_id
        visited: set[int] = set()
        while parent_id is not None and parent_id not in visited:
            if parent_id == ancestor.service_id:
                return True
            visited.add(parent_id)
            parent = entries.get(parent_id)
            parent_id = parent.parent_service_id if parent is not None else None
        return False

    @staticmethod
    def _select_advisory_target(
        case_type: str | None,
        source: ServiceCatalogEntry | None,
        entries: Iterable[ServiceCatalogEntry],
    ) -> dict[str, Any]:
        """Select observable target candidates without granting execution permission."""
        profile = CaseProfileRegistry().get(case_type or "")
        if profile is None:
            return {
                "target_selection_state": TargetSelectionState.not_found,
                "target_selection_method": "case_profile_catalog",
                "catalog_state": "available",
            }

        ranked: dict[int, tuple[int, ServiceTargetCandidate]] = {}
        normalized_terms = tuple(term.casefold().replace("ё", "е") for term in profile.service_name_terms)
        for entry in entries:
            normalized_path = entry.service_path.casefold().replace("ё", "е")
            evidence: list[str] = []
            score = 0
            if entry.service_id in profile.exact_service_ids:
                score += 100
                evidence.append("case_profile_exact_service_id")
            matched_terms = [term for term in normalized_terms if term and term in normalized_path]
            if matched_terms:
                score += 20 * len(matched_terms)
                evidence.extend(f"service_path_term:{term}" for term in matched_terms)
            if score:
                ranked[entry.service_id] = (
                    score,
                    ServiceTargetCandidate(
                        service_id=entry.service_id,
                        service_path=entry.service_path,
                        confidence="high" if score >= 100 else "medium",
                        evidence=evidence,
                    ),
                )

        ordered = [item[1] for item in sorted(ranked.values(), key=lambda item: (-item[0], item[1].service_path))]
        candidates = ordered[:5]
        selected: ServiceTargetCandidate | None = None
        selection_state = TargetSelectionState.not_found
        if source is not None:
            selected = next((item for item in candidates if item.service_id == source.service_id), None)
            if selected is not None:
                selection_state = TargetSelectionState.source_match
        if selected is None and len(candidates) == 1:
            selected = candidates[0]
            selection_state = TargetSelectionState.target_suggested
        elif selected is None and len(candidates) > 1:
            selection_state = TargetSelectionState.ambiguous

        return {
            "target_service_id": selected.service_id if selected else None,
            "target_service_path": selected.service_path if selected else None,
            "target_selection_state": selection_state,
            "target_selection_method": "case_profile_catalog",
            "target_candidates": candidates,
            "catalog_state": "available",
        }

    @staticmethod
    def _degraded(
        decision: CaseDecision, service_id: int | None, component: str, reason: str
    ) -> ServiceCompatibilityDecision:
        return ServiceCompatibilityDecision(
            task_id=decision.task_id,
            snapshot_hash=decision.snapshot_hash,
            case_decision_id=decision.id,
            source_service_id=service_id,
            catalog_state="unavailable",
            state=ServiceCompatibilityState.degraded,
            degraded_component=component,
            reason_codes=[reason],
            authorization_state=reason,
        )


class LLMServiceTargetReranker:
    """Selects only from the bounded catalog shortlist produced by RedirectResolver."""

    prompt_version = "service-target-reranker-v1"

    def __init__(
        self,
        transport: LLMTransport,
        *,
        model_alias: str = "helpdesk-fast",
        timeout_seconds: float = 12.0,
    ) -> None:
        self.transport = transport
        self.model_alias = model_alias
        self.timeout_seconds = timeout_seconds
        self.sanitizer = PIISanitizer(max_length=6000)

    async def rerank(
        self,
        snapshot: TicketSnapshot,
        resolution: TargetServiceResolution,
    ) -> TargetServiceResolution:
        if resolution.state not in {TargetSelectionState.ambiguous, TargetSelectionState.not_found}:
            return resolution
        if not resolution.candidates:
            return resolution

        allowed = {candidate.service_id: candidate for candidate in resolution.candidates}
        payload = {
            "public_text": {
                "title": self._sanitize(snapshot.title),
                "description": self._sanitize(snapshot.description),
                "comments": [
                    self._sanitize(item.text) for item in snapshot.public_comments if not item.is_private
                ],
            },
            "source_service": {
                "service_id": resolution.source_service_id,
                "service_path": resolution.source_service_path,
            },
            "candidates": [
                {
                    "service_id": candidate.service_id,
                    "service_path": candidate.service_path,
                    "retrieval_score": candidate.score,
                    "evidence": candidate.evidence,
                }
                for candidate in resolution.candidates
            ],
        }
        try:
            raw = await self.transport.complete_json(
                model_alias=self.model_alias,
                system_prompt=(
                    "Choose the best target helpdesk service using only the supplied candidates and literal "
                    "public ticket text. Return JSON with exactly: verdict, selected_service_id, confidence, "
                    "reason. verdict must be selected, ambiguous, or insufficient. For selected, "
                    "selected_service_id must be one supplied ID and confidence must be 0..1. "
                    "Never invent a service, action, fact, or credential."
                ),
                payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or set(parsed) - {
                "verdict",
                "selected_service_id",
                "confidence",
                "reason",
            }:
                raise ValueError("Invalid target reranker schema")
            verdict = str(parsed.get("verdict", ""))
            if verdict not in {"selected", "ambiguous", "insufficient"}:
                raise ValueError("Invalid target reranker verdict")
            reason = str(parsed.get("reason", ""))[:500]
            confidence = float(parsed.get("confidence", 0.0))
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("Invalid target reranker confidence")

            if verdict == "selected":
                selected_id = int(parsed.get("selected_service_id"))
                selected = allowed.get(selected_id)
                if selected is None:
                    raise ValueError("Target reranker changed candidate set")
                if confidence < 0.65:
                    return resolution.model_copy(
                        update={
                            "llm_used": True,
                            "llm_verdict": "insufficient",
                            "llm_reason": reason or "confidence_below_threshold",
                            "llm_confidence": confidence,
                            "reason_codes": [*resolution.reason_codes, "target_reranker_low_confidence"],
                        }
                    )
                state = (
                    TargetSelectionState.source_match
                    if selected_id == resolution.source_service_id
                    else TargetSelectionState.target_suggested
                )
                return resolution.model_copy(
                    update={
                        "state": state,
                        "selected_service_id": selected.service_id,
                        "selected_service_path": selected.service_path,
                        "method": "catalog_top10_llm_rerank",
                        "evidence": [*selected.evidence, "llm_rerank_selected"],
                        "reason_codes": ["llm_selected_catalog_target"],
                        "llm_used": True,
                        "llm_verdict": verdict,
                        "llm_reason": reason,
                        "llm_confidence": confidence,
                    }
                )

            return resolution.model_copy(
                update={
                    "llm_used": True,
                    "llm_verdict": verdict,
                    "llm_reason": reason,
                    "llm_confidence": confidence,
                    "reason_codes": [*resolution.reason_codes, f"target_reranker_{verdict}"],
                }
            )
        except (LLMTransportError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return resolution.model_copy(
                update={
                    "llm_used": True,
                    "llm_verdict": "degraded",
                    "llm_reason": "target_reranker_unavailable_or_invalid",
                    "reason_codes": [*resolution.reason_codes, "target_reranker_degraded"],
                }
            )

    def _sanitize(self, text: str) -> str:
        safe = self.sanitizer.sanitize(text or "").sanitized_text
        return _SECRET_RE.sub("[SECRET]", safe)


def build_redirect_plan(compatibility: ServiceCompatibilityDecision, decision: CaseDecision) -> RedirectPlan:
    if compatibility.state != ServiceCompatibilityState.mismatch or len(compatibility.candidates) != 1:
        raise ValueError("redirect_plan_requires_single_valid_target")
    target = compatibility.candidates[0]
    if compatibility.source_service_id is None or not compatibility.catalog_hash:
        raise ValueError("redirect_plan_requires_bound_source_and_catalog")
    comment = (
        "Заявка отменена, так как создана не в правильном разделе. "
        f"Требуется оставить заявку в разделе: {target.service_path}."
    )
    executable = target.redirect_strategy == RedirectStrategy.cancel_and_recreate
    draft = RedirectPlan(
        task_id=decision.task_id,
        snapshot_hash=decision.snapshot_hash,
        case_decision_id=decision.id,
        compatibility_decision_id=compatibility.id,
        source_service_id=compatibility.source_service_id,
        target_service_id=target.service_id,
        target_service_path=target.service_path,
        strategy=target.redirect_strategy,
        template_key="wrong_service_cancel_v1",
        rendered_public_comment=comment,
        catalog_hash=compatibility.catalog_hash,
        binding_key=target.binding_key,
        binding_version=target.binding_version,
        state=RedirectPlanState.ready if executable else RedirectPlanState.manual,
        approval_state="pending" if executable else "unsupported",
        execution_state="not_started" if executable else "unsupported",
    )
    return draft.model_copy(update={"plan_hash": compute_redirect_plan_hash(draft)})
