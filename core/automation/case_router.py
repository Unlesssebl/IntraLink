"""Evidence-based intake router that selects case types, never capabilities."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Protocol

from core.automation.case_profiles import CaseProfileRegistry, CaseTypeProfile
from core.automation.contracts import (
    CaseCandidate,
    CaseDecision,
    CaseDecisionState,
    CaseEvidence,
    CaseFrame,
    TicketSnapshot,
)


class CaseVerifier(Protocol):
    prompt_version: str

    async def verify(
        self,
        snapshot: TicketSnapshot,
        frame: CaseFrame,
        candidates: list[CaseCandidate],
    ) -> tuple[dict[str, str], dict[str, object]]: ...


class CaseRouter:
    version = "case-router-v1"

    def __init__(self, profiles: CaseProfileRegistry | None = None, verifier: CaseVerifier | None = None) -> None:
        self.profiles = profiles or CaseProfileRegistry()
        self.verifier = verifier

    async def decide(
        self,
        snapshot: TicketSnapshot,
        frame: CaseFrame,
        *,
        allow_verifier: bool = True,
    ) -> CaseDecision:
        evidence = self._collect_evidence(snapshot, frame)
        grouped: dict[str, list[CaseEvidence]] = defaultdict(list)
        for item in evidence:
            grouped[item.candidate_key].append(item)
        candidates = [
            CaseCandidate(
                case_type=key,
                case_type_version=(self.profiles.get(key) or CaseTypeProfile(
                    case_type=key, title=key, intent_summary=key
                )).version,
                evidence_ids=sorted(item.id for item in items if item.polarity == "supports"),
                contradiction_ids=sorted(item.id for item in items if item.polarity == "contradicts"),
            )
            for key, items in grouped.items()
            if any(item.polarity == "supports" for item in items)
        ]
        candidates.sort(key=lambda item: item.case_type)

        if not candidates:
            if frame.degraded_components:
                return self._decision(
                    snapshot,
                    frame,
                    CaseDecisionState.degraded,
                    candidates,
                    evidence,
                    reason_codes=["case_generation_degraded"],
                    degradation_reason="case_generation_degraded",
                )
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.unknown,
                candidates,
                evidence,
                reason_codes=["no_case_type_evidence"],
            )

        explicit_intents = self._explicit_intent_candidates(candidates, evidence)
        if len(explicit_intents) > 1:
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.multi_intent,
                candidates,
                evidence,
                primary=explicit_intents[0],
                secondary=explicit_intents[1:],
                reason_codes=["multiple_explicit_intents"],
            )
        dominant = self._dominant_candidates(candidates, evidence)
        if len(dominant) == 1:
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.selected,
                candidates,
                evidence,
                primary=dominant[0],
                reason_codes=["direct_case_evidence"],
            )
        if len(dominant) > 1:
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.multi_intent,
                candidates,
                evidence,
                primary=dominant[0],
                secondary=dominant[1:],
                reason_codes=["multiple_explicit_intents"],
            )
        if len(candidates) > 3:
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.ambiguous,
                candidates,
                evidence,
                reason_codes=["candidate_set_too_broad"],
            )
        if self.verifier is None or not allow_verifier:
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.ambiguous,
                candidates,
                evidence,
                reason_codes=["case_verifier_required"],
            )

        try:
            verdicts, trace = await self.verifier.verify(snapshot, frame, candidates)
        except Exception:
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.degraded,
                candidates,
                evidence,
                reason_codes=["case_verifier_degraded"],
                degradation_reason="case_verifier_degraded",
            )
        candidate_keys = {item.case_type for item in candidates}
        if set(verdicts) != candidate_keys or any(value not in {"supported", "contradicted", "insufficient"} for value in verdicts.values()):
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.degraded,
                candidates,
                evidence,
                reason_codes=["case_verifier_invalid_result"],
                degradation_reason="case_verifier_invalid_result",
                trace=trace,
            )
        supported = sorted(key for key, verdict in verdicts.items() if verdict == "supported")
        if len(supported) == 1:
            return self._decision(
                snapshot,
                frame,
                CaseDecisionState.selected,
                candidates,
                evidence,
                primary=supported[0],
                reason_codes=["case_verifier_single_supported"],
                trace=trace,
            )
        state = CaseDecisionState.multi_intent if len(supported) > 1 else CaseDecisionState.ambiguous
        return self._decision(
            snapshot,
            frame,
            state,
            candidates,
            evidence,
            primary=supported[0] if supported else None,
            secondary=supported[1:] if len(supported) > 1 else [],
            reason_codes=["case_verifier_multiple_supported" if supported else "case_verifier_insufficient"],
            trace=trace,
        )

    def _collect_evidence(self, snapshot: TicketSnapshot, frame: CaseFrame) -> list[CaseEvidence]:
        evidence: dict[str, CaseEvidence] = {}
        targets = [("title", snapshot.title), ("description", snapshot.description)]
        targets.extend((f"comment:{item.id}", item.text) for item in snapshot.public_comments if not item.is_private)
        for profile in self.profiles.list_all():
            if snapshot.service_id is not None and snapshot.service_id in profile.exact_service_ids:
                item = self._evidence(snapshot, profile.case_type, "service_id", "supports", "exact", f"service_id:{snapshot.service_id}")
                evidence[item.id] = item
            service_name = snapshot.service_name or ""
            for term in profile.service_name_terms:
                span = self._find_span(service_name, term)
                if span:
                    item = self._evidence(snapshot, profile.case_type, "service_name", "supports", "strong", f"service_name:{term}", span)
                    evidence[item.id] = item
                    break
            for phrase in profile.lexical_phrases:
                for source_ref, text in targets:
                    span = self._find_span(text, phrase)
                    if span:
                        item = self._evidence(snapshot, profile.case_type, source_ref.split(":")[0], "supports", "strong", source_ref, span)
                        evidence[item.id] = item
        for assertion in frame.assertions:
            for profile in self.profiles.list_all():
                if assertion.key in profile.assertion_keys:
                    item = self._evidence(
                        snapshot,
                        profile.case_type,
                        "case_assertion",
                        "contradicts" if assertion.is_negated else "supports",
                        "strong",
                        assertion.source_ref,
                        assertion.text_span,
                    )
                    evidence[item.id] = item
        return sorted(evidence.values(), key=lambda item: item.id)

    @staticmethod
    def _dominant_candidates(candidates: list[CaseCandidate], evidence: list[CaseEvidence]) -> list[str]:
        by_id = {item.id: item for item in evidence}
        direct: list[str] = []
        for candidate in candidates:
            supporting = [by_id[item_id] for item_id in candidate.evidence_ids]
            contradictions = [by_id[item_id] for item_id in candidate.contradiction_ids]
            if contradictions:
                continue
            has_exact = any(item.source == "service_id" and item.strength == "exact" for item in supporting)
            distinct_sources = {item.source for item in supporting if item.source != "semantic"}
            if has_exact or len(distinct_sources) >= 2:
                direct.append(candidate.case_type)
        return sorted(direct)

    @staticmethod
    def _explicit_intent_candidates(candidates: list[CaseCandidate], evidence: list[CaseEvidence]) -> list[str]:
        by_id = {item.id: item for item in evidence}
        explicit: list[str] = []
        spans: set[str] = set()
        for candidate in candidates:
            lexical = [
                by_id[item_id]
                for item_id in candidate.evidence_ids
                if by_id[item_id].source in {"title", "description", "comment"}
                and by_id[item_id].strength == "strong"
                and by_id[item_id].text_span
            ]
            if not lexical or candidate.contradiction_ids:
                continue
            candidate_spans = {item.text_span.casefold() for item in lexical if item.text_span}
            if candidate_spans - spans:
                explicit.append(candidate.case_type)
                spans.update(candidate_spans)
        return sorted(explicit)

    def _decision(
        self,
        snapshot: TicketSnapshot,
        frame: CaseFrame,
        state: CaseDecisionState,
        candidates: list[CaseCandidate],
        evidence: list[CaseEvidence],
        *,
        primary: str | None = None,
        secondary: list[str] | None = None,
        reason_codes: list[str],
        degradation_reason: str | None = None,
        trace: dict[str, object] | None = None,
    ) -> CaseDecision:
        return CaseDecision(
            task_id=snapshot.task_id,
            snapshot_hash=snapshot.snapshot_hash,
            frame_id=frame.id,
            router_version=self.version,
            prompt_version=self.verifier.prompt_version if self.verifier is not None and trace is not None else None,
            state=state,
            primary_case_type=primary,
            secondary_case_types=secondary or [],
            candidates=candidates,
            evidence=evidence,
            reason_codes=reason_codes,
            degradation_reason=degradation_reason,
            verifier_trace=trace,
        )

    @staticmethod
    def _find_span(text: str, phrase: str) -> str | None:
        normalized = (text or "").casefold().replace("ё", "е")
        target = phrase.casefold().replace("ё", "е")
        match = re.search(rf"(?<!\w){re.escape(target)}(?!\w)", normalized)
        return (text or "")[match.start() : match.end()] if match else None

    @staticmethod
    def _evidence(
        snapshot: TicketSnapshot,
        case_type: str,
        source: str,
        polarity: str,
        strength: str,
        source_ref: str,
        text_span: str | None = None,
    ) -> CaseEvidence:
        payload = "|".join([snapshot.snapshot_hash, case_type, source, polarity, strength, source_ref, text_span or ""])
        return CaseEvidence(
            id=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            candidate_key=case_type,
            source=source,
            polarity=polarity,
            strength=strength,
            source_ref=source_ref,
            text_span=text_span,
        )
