"""Coherence Guard for IntraLink v2 Autopilot.

Detects semantic collisions and divergence between the selected service catalog
and the actual textual body/extracted entities of the ticket.
"""

import logging
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from core.intraservice.dto import TaskDTO

logger = logging.getLogger("core.scenarios.coherence_guard")


class CoherenceStatus(str, Enum):
    COHERENT = "COHERENT"
    DIVERGENT = "DIVERGENT"
    NEUTRAL = "NEUTRAL"


class CoherenceResult(BaseModel):
    """Result of analyzing coherence between catalog prior and ticket content."""

    status: CoherenceStatus = Field(..., description="COHERENT, DIVERGENT or NEUTRAL")
    confidence_delta: float = Field(default=0.0, description="Score delta (positive for bonus, negative for penalty)")
    divergent_scenario: Optional[str] = Field(default=None, description="Actual intent scenario detected in text if divergent")
    reasons: List[str] = Field(default_factory=list, description="Explanations of match or conflict")


# Strong textual keywords representing specific scenario domains
SCENARIO_SIGNALS = {
    "install_printer": ["принтер", "мфу", "печать", "печатает", "драйвер", "кэнон", "canon", "kyocera", "hp laserjet", "pantum"],
    "grant_wlan": ["wi-fi", "wifi", "вайфай", "беспроводн", "wlan-worknet", "wlan"],
    "offline_host": ["не включается", "компьютер не реагирует", "нет питания", "черный экран", "гудит но не включается", "запах гари"],
    "service_redirect": ["1с", "directum", "бухгалтер", "стул", "клининг", "пропуск", "канцеляр"],
}


class CoherenceGuard:
    """Evaluates cross-domain contradictions between service catalog and ticket content."""

    def evaluate(
        self,
        candidate_key: str,
        task: TaskDTO,
        semantic_scores: Optional[Dict[str, float]] = None,
    ) -> CoherenceResult:
        """Check whether candidate scenario agrees with ticket text, entities and semantic affinity."""
        text = f"{task.name or ''} {task.description or ''}".lower()

        # 1. Check if candidate scenario keywords appear in text
        candidate_keywords = SCENARIO_SIGNALS.get(candidate_key, [])
        matches_candidate = any(kw in text for kw in candidate_keywords)

        # 2. Check semantic prototype divergence if scores provided
        if semantic_scores and not matches_candidate:
            cand_sem = semantic_scores.get(candidate_key, 0.0)
            for other_key, other_sem in semantic_scores.items():
                if other_key != candidate_key and other_sem >= 0.85 and cand_sem <= 0.30:
                    return CoherenceResult(
                        status=CoherenceStatus.DIVERGENT,
                        confidence_delta=-0.40,
                        divergent_scenario=other_key,
                        reasons=[
                            f"Semantic prototype divergence: '{candidate_key}' (score={cand_sem:.2f}) "
                            f"conflicts with high-affinity '{other_key}' (score={other_sem:.2f})"
                        ],
                    )

        # 3. Check if a DIFFERENT scenario has strong contradicting lexical signals
        competing_matches = {}
        for other_key, kws in SCENARIO_SIGNALS.items():
            if other_key != candidate_key:
                found = [kw for kw in kws if kw in text]
                if found:
                    competing_matches[other_key] = found

        # 4. Analyze collisions
        if matches_candidate and not competing_matches:
            # Full coherence: Catalog matches text, no competitor
            return CoherenceResult(
                status=CoherenceStatus.COHERENT,
                confidence_delta=0.10,
                reasons=[f"Ticket text confirms scenario '{candidate_key}'"],
            )

        if not matches_candidate and competing_matches:
            # Clear divergence: Candidate keywords absent, but strong signals for another scenario exist
            strongest_competitor, matched_words = max(competing_matches.items(), key=lambda item: len(item[1]))
            logger.warning(
                "Divergence detected in ticket #%s: candidate '%s' conflicts with text signals for '%s' (%s)",
                task.id,
                candidate_key,
                strongest_competitor,
                matched_words,
            )
            return CoherenceResult(
                status=CoherenceStatus.DIVERGENT,
                confidence_delta=-0.40,
                divergent_scenario=strongest_competitor,
                reasons=[
                    f"Catalog suggests '{candidate_key}', but text strongly indicates '{strongest_competitor}' (words: {matched_words})"
                ],
            )

        if matches_candidate and competing_matches:
            # Ambiguity: Contains words for multiple scenarios
            return CoherenceResult(
                status=CoherenceStatus.NEUTRAL,
                confidence_delta=-0.05,
                reasons=[f"Text mentions elements of '{candidate_key}' and competing domains {list(competing_matches.keys())}"],
            )

        # Neutral fallback
        return CoherenceResult(
            status=CoherenceStatus.NEUTRAL,
            confidence_delta=0.0,
            reasons=["No decisive domain keywords found to confirm or reject candidate"],
        )
