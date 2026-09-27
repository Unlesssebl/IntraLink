"""CatalogCandidateProvider for Evidence-Based Routing Cascade.

Evaluates deterministic structured signals from IntraService catalog:
exact service_id and normalized service_name. Does not apply Bayesian weights
or select a winning scenario.
"""

import re
from typing import List, Sequence

from core.routing.contracts import (
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingEvidence,
    TicketSnapshot,
)
from core.routing.evidence_id import compute_evidence_id
from core.routing.profiles import ScenarioRoutingProfile


def _normalize_text(s: str) -> str:
    """Normalize text with Unicode casefold, space collapsing, and ё->е."""
    return " ".join(s.casefold().replace("ё", "е").split())


def _build_term_regex(term: str) -> re.Pattern[str]:
    """Compile term regex treating 'е' and 'ё' identically and matching flexible whitespace."""
    norm_term = _normalize_text(term)
    escaped = re.escape(norm_term)
    # Allow 'е' to match both 'е' and 'ё'
    pattern_str = escaped.replace(r"\ ", r"\s+").replace("е", "[её]")
    return re.compile(pattern_str, re.IGNORECASE)


class CatalogCandidateProvider:
    """Deterministic candidate provider for service catalog signals."""

    name: str = "catalog"

    async def collect(
        self,
        snapshot: TicketSnapshot,
        profiles: Sequence[ScenarioRoutingProfile],
    ) -> List[RoutingEvidence]:
        """Collect atomic evidence from exact service_id and service_name matches."""
        evidence: List[RoutingEvidence] = []

        sid = snapshot.service_id
        sname = snapshot.service_name

        for profile in profiles:
            # 1. Exact service_id match
            if sid is not None and sid in profile.exact_service_ids:
                source_ref = f"service_id:{sid}"
                ev_id = compute_evidence_id(
                    provider_name=self.name,
                    snapshot_hash=snapshot.snapshot_hash,
                    candidate_key=profile.scenario_key,
                    source=EvidenceSource.service_id,
                    polarity=EvidencePolarity.supports,
                    strength=EvidenceStrength.exact,
                    source_ref=source_ref,
                    text_span=None,
                )
                evidence.append(
                    RoutingEvidence(
                        id=ev_id,
                        candidate_key=profile.scenario_key,
                        source=EvidenceSource.service_id,
                        polarity=EvidencePolarity.supports,
                        strength=EvidenceStrength.exact,
                        source_ref=source_ref,
                        text_span=None,
                    )
                )

            # 2. Normalized service_name match
            if sname and sname.strip():
                for term in profile.service_name_terms:
                    reg = _build_term_regex(term)
                    match = reg.search(sname)
                    if match:
                        matched_span = sname[match.start() : match.end()]
                        source_ref = f"service_name:{term}"
                        ev_id = compute_evidence_id(
                            provider_name=self.name,
                            snapshot_hash=snapshot.snapshot_hash,
                            candidate_key=profile.scenario_key,
                            source=EvidenceSource.service_name,
                            polarity=EvidencePolarity.supports,
                            strength=EvidenceStrength.strong,
                            source_ref=source_ref,
                            text_span=matched_span,
                        )
                        evidence.append(
                            RoutingEvidence(
                                id=ev_id,
                                candidate_key=profile.scenario_key,
                                source=EvidenceSource.service_name,
                                polarity=EvidencePolarity.supports,
                                strength=EvidenceStrength.strong,
                                source_ref=source_ref,
                                text_span=matched_span,
                            )
                        )
                        # One match per term is sufficient
                        break

        return evidence
