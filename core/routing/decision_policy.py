"""Deterministic decision arbitration and GreyZonePolicy for Evidence-Based Routing Cascade.

Enforces zero-additive scoring, evidence-class based arbitration, strict machine reason codes,
and invariant compliance without probabilities or subjective confidence metrics.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from core.routing.contracts import (
    CandidateVerification,
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingEvidence,
    RoutingState,
    ScenarioCandidate,
    VerificationVerdict,
    VerifierTrace,
)
from core.routing.profile_registry import (
    RoutingProfileRegistry,
    get_default_profile_registry,
)

logger = logging.getLogger("core.routing.decision_policy")


@dataclass(frozen=True)
class DirectSelectionResult:
    """Outcome of direct deterministic candidate evaluation."""

    candidate: ScenarioCandidate
    reason_code: str


@dataclass(frozen=True)
class GreyZoneOutcome:
    """Outcome of GreyZone policy evaluation before potential verifier execution."""

    direct_winner: DirectSelectionResult | None
    eligible_candidates: list[ScenarioCandidate]
    terminal_state: RoutingState | None
    terminal_reason_codes: list[str]
    terminal_degradation_reason: str | None = None


@dataclass(frozen=True)
class ArbitrationResult:
    """Result of post-verification or direct arbitration."""

    state: RoutingState
    selected_candidate: ScenarioCandidate | None
    reason_codes: list[str]
    degradation_reason: str | None = None


class GreyZonePolicy:
    """Evaluates candidates to determine whether a direct deterministic winner exists

    or whether the candidates must be passed to the LLM Grey Zone Verifier.
    """

    def __init__(self, profile_registry: RoutingProfileRegistry | None = None) -> None:
        self._profiles = profile_registry or get_default_profile_registry()

    def evaluate(
        self,
        candidates: Sequence[ScenarioCandidate],
        evidence: Sequence[RoutingEvidence],
        degraded_providers: dict[str, str],
    ) -> GreyZoneOutcome:
        """Arbitrate candidate dominance or configure grey zone verification."""
        evidence_by_id: dict[str, RoutingEvidence] = {e.id: e for e in evidence}

        # 1. Inspect contradictions and categorize candidates
        eligible: list[ScenarioCandidate] = []
        for cand in candidates:
            contra_items = [evidence_by_id[cid] for cid in cand.contradiction_ids if cid in evidence_by_id]
            has_fatal_contradiction = any(
                c.polarity == EvidencePolarity.contradicts
                and c.strength in (EvidenceStrength.exact, EvidenceStrength.strong)
                for c in contra_items
            )
            # Candidate with exact or strong contradiction cannot win
            if not has_fatal_contradiction and cand.evidence_ids:
                eligible.append(cand)

        # 2. Check for a single dominant direct candidate
        direct_winner = self._find_direct_winner(candidates, eligible, evidence_by_id)
        if direct_winner is not None:
            return GreyZoneOutcome(
                direct_winner=direct_winner,
                eligible_candidates=eligible,
                terminal_state=None,
                terminal_reason_codes=[],
                terminal_degradation_reason=None,
            )

        # 3. If no direct winner, evaluate grey zone bounds
        if not eligible:
            if degraded_providers:
                return GreyZoneOutcome(
                    direct_winner=None,
                    eligible_candidates=[],
                    terminal_state=RoutingState.degraded,
                    terminal_reason_codes=["candidate_generation_degraded"],
                    terminal_degradation_reason="candidate_generation_degraded",
                )
            return GreyZoneOutcome(
                direct_winner=None,
                eligible_candidates=[],
                terminal_state=RoutingState.unmatched,
                terminal_reason_codes=["no_candidates_found"],
                terminal_degradation_reason=None,
            )

        if len(eligible) > 3:
            return GreyZoneOutcome(
                direct_winner=None,
                eligible_candidates=eligible,
                terminal_state=RoutingState.ambiguous,
                terminal_reason_codes=["candidate_set_too_broad"],
                terminal_degradation_reason=None,
            )

        # 1..3 eligible candidates require verifier
        return GreyZoneOutcome(
            direct_winner=None,
            eligible_candidates=eligible,
            terminal_state=None,
            terminal_reason_codes=[],
            terminal_degradation_reason=None,
        )

    def _find_direct_winner(
        self,
        all_candidates: Sequence[ScenarioCandidate],
        eligible_candidates: Sequence[ScenarioCandidate],
        evidence_by_id: dict[str, RoutingEvidence],
    ) -> DirectSelectionResult | None:
        """Determine if a single dominant candidate satisfies direct selection rules."""
        dominant_candidates: list[DirectSelectionResult] = []

        for cand in eligible_candidates:
            profile = self._profiles.get(cand.scenario_key)
            # consultation_only is never selected directly
            if profile is not None and profile.consultation_only:
                continue

            # Candidate must have zero contradictions (weak contradiction moves competition to grey zone)
            if cand.contradiction_ids:
                continue

            sup_evidence = [evidence_by_id[eid] for eid in cand.evidence_ids if eid in evidence_by_id]
            if not sup_evidence:
                continue

            # Check Condition 1: service_id with exact strength
            has_exact_service_id = any(
                e.source == EvidenceSource.service_id and e.strength == EvidenceStrength.exact
                for e in sup_evidence
            )

            # Check Condition 2: confirmed by >= 2 distinct non-semantic sources
            non_semantic_sources: set[EvidenceSource] = {
                e.source for e in sup_evidence if e.source != EvidenceSource.semantic
            }
            has_multi_source = len(non_semantic_sources) >= 2

            if not (has_exact_service_id or has_multi_source):
                continue

            # Check all other candidates: must have ONLY semantic or weak evidence
            all_others_weak_or_semantic = True
            for other in all_candidates:
                if other.scenario_key == cand.scenario_key:
                    continue
                other_sup = [evidence_by_id[eid] for eid in other.evidence_ids if eid in evidence_by_id]
                for o_ev in other_sup:
                    if o_ev.source != EvidenceSource.semantic and o_ev.strength != EvidenceStrength.weak:
                        all_others_weak_or_semantic = False
                        break
                if not all_others_weak_or_semantic:
                    break

            if all_others_weak_or_semantic:
                rc = "direct_exact_service_id" if has_exact_service_id else "direct_multi_source_evidence"
                dominant_candidates.append(DirectSelectionResult(candidate=cand, reason_code=rc))

        if len(dominant_candidates) == 1:
            return dominant_candidates[0]

        return None


class DecisionPolicy:
    """Arbitrates final scenario selection from verifier results or grey zone policies."""

    def __init__(self, profile_registry: RoutingProfileRegistry | None = None) -> None:
        self._profiles = profile_registry or get_default_profile_registry()
        self._grey_zone = GreyZonePolicy(profile_registry=self._profiles)

    @property
    def grey_zone_policy(self) -> GreyZonePolicy:
        return self._grey_zone

    def arbitrate_verifier_result(
        self,
        verifications: Sequence[CandidateVerification],
        verifier_trace: VerifierTrace,
        eligible_candidates: Sequence[ScenarioCandidate],
    ) -> ArbitrationResult:
        """Process verifier outcome according to domain transition table."""
        if verifier_trace.status == "degraded":
            deg_reason = verifier_trace.degradation_reason or "verifier_unavailable"
            return ArbitrationResult(
                state=RoutingState.degraded,
                selected_candidate=None,
                reason_codes=["verifier_degraded", deg_reason],
                degradation_reason=deg_reason,
            )

        cand_by_key = {c.scenario_key: c for c in eligible_candidates}

        supported_keys = [v.scenario_key for v in verifications if v.verdict == VerificationVerdict.supported]
        insufficient_keys = [v.scenario_key for v in verifications if v.verdict == VerificationVerdict.insufficient]
        contradicted_keys = [v.scenario_key for v in verifications if v.verdict == VerificationVerdict.contradicted]

        # 1. Exactly one supported -> select it
        if len(supported_keys) == 1:
            winner_key = supported_keys[0]
            winner_cand = cand_by_key.get(winner_key)
            if winner_cand is not None:
                return ArbitrationResult(
                    state=RoutingState.selected,
                    selected_candidate=winner_cand,
                    reason_codes=["verifier_arbitration", "single_supported_candidate"],
                )

        # 2. Multiple supported -> ambiguous
        if len(supported_keys) > 1:
            return ArbitrationResult(
                state=RoutingState.ambiguous,
                selected_candidate=None,
                reason_codes=["verifier_arbitration", "multiple_supported_candidates"],
            )

        # 3. Zero supported, but at least one insufficient -> ambiguous
        if not supported_keys and insufficient_keys:
            return ArbitrationResult(
                state=RoutingState.ambiguous,
                selected_candidate=None,
                reason_codes=["verifier_arbitration", "insufficient_evidence"],
            )

        # 4. All candidates contradicted -> unmatched
        if len(contradicted_keys) == len(verifications) and not supported_keys:
            return ArbitrationResult(
                state=RoutingState.unmatched,
                selected_candidate=None,
                reason_codes=["verifier_arbitration", "all_candidates_contradicted"],
            )

        # Fallback if unhandled verifier combo
        return ArbitrationResult(
            state=RoutingState.ambiguous,
            selected_candidate=None,
            reason_codes=["verifier_arbitration", "insufficient_evidence"],
        )
