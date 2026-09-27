"""LexicalCandidateProvider for Evidence-Based Routing Cascade.

Extracts deterministic keyword and phrase evidence from ticket title,
description, and public comments with strict word boundaries and exact text spans.
Does not inspect private comments or custom fields as raw text.
"""

import re
from typing import List, Sequence, Tuple

from core.routing.contracts import (
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingEvidence,
    TicketSnapshot,
)
from core.routing.evidence_id import compute_evidence_id
from core.routing.profiles import ScenarioRoutingProfile


def _normalize_phrase(s: str) -> str:
    """Normalize phrase for regex generation (casefold, space collapse, ё->е)."""
    return " ".join(s.casefold().replace("ё", "е").split())


def _build_lexical_regex(phrase: str) -> re.Pattern[str]:
    """Compile regex with word boundaries, flexible whitespace and ё/е equivalence."""
    norm = _normalize_phrase(phrase)
    escaped = re.escape(norm)
    pattern_core = escaped.replace(r"\ ", r"\s+").replace("е", "[её]")
    # \w in Python covers [a-zA-Z0-9_] and Unicode word characters
    full_pattern = rf"(?<!\w){pattern_core}(?!\w)"
    return re.compile(full_pattern, re.IGNORECASE)


class LexicalCandidateProvider:
    """Deterministic candidate provider for keyword and phrase evidence."""

    name: str = "lexical"

    async def collect(
        self,
        snapshot: TicketSnapshot,
        profiles: Sequence[ScenarioRoutingProfile],
    ) -> List[RoutingEvidence]:
        """Collect atomic evidence from title, description and public comments."""
        evidence: List[RoutingEvidence] = []

        # Texts to scan: list of (source, source_ref, text)
        targets: List[Tuple[EvidenceSource, str, str]] = []

        if snapshot.title and snapshot.title.strip():
            targets.append((EvidenceSource.title, "title", snapshot.title))

        if snapshot.description and snapshot.description.strip():
            targets.append((EvidenceSource.description, "description", snapshot.description))

        for idx, comment in enumerate(snapshot.public_comments):
            if comment.is_private:
                continue
            if comment.text and comment.text.strip():
                ref = f"comment:{comment.id}" if comment.id is not None else f"comment:idx_{idx}"
                targets.append((EvidenceSource.comment, ref, comment.text))

        for profile in profiles:
            for phrase in profile.lexical_phrases:
                reg = _build_lexical_regex(phrase)
                for source, source_ref, raw_text in targets:
                    match = reg.search(raw_text)
                    if match:
                        text_span = raw_text[match.start() : match.end()]
                        ev_id = compute_evidence_id(
                            provider_name=self.name,
                            snapshot_hash=snapshot.snapshot_hash,
                            candidate_key=profile.scenario_key,
                            source=source,
                            polarity=EvidencePolarity.supports,
                            strength=EvidenceStrength.strong,
                            source_ref=source_ref,
                            text_span=text_span,
                        )
                        evidence.append(
                            RoutingEvidence(
                                id=ev_id,
                                candidate_key=profile.scenario_key,
                                source=source,
                                polarity=EvidencePolarity.supports,
                                strength=EvidenceStrength.strong,
                                source_ref=source_ref,
                                text_span=text_span,
                            )
                        )

        return evidence
