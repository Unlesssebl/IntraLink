"""Small deterministic intake helpers independent from workflow execution."""

from __future__ import annotations

import re

from core.automation.case_router import CaseRouter
from core.automation.contracts import CaseDecision, CaseDecisionState, CaseFrame, TicketSnapshot
from core.automation.frame_extractor import CaseFrameExtractor
from core.automation.service_routing import TargetServiceResolution

TENSE_PATTERNS = [
    re.compile(
        r"\b(?:срочно|срочнейше|срочный|горит|горят|отчет\s+горит|работа\s+стоит|встала\s+работа|не\s+могу\s+работать|критично|asap|sos)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:сколько\s+можно\s+ждать|доколе|когда\s+уже|почему\s+так\s+долго|возмутительно|безобразие|караул|беспредел)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:повторно\s+пишу|третий\s+раз\s+пишу|шеф\s+ругается|начальство\s+требует)\b", re.IGNORECASE),
    re.compile(r"!{2,}"),
]


class CaseIntakeEngine:
    """Deterministic-first intake with LLM only for unresolved cases."""

    def __init__(self, extractor: CaseFrameExtractor, router: CaseRouter) -> None:
        self.extractor = extractor
        self.router = router

    async def analyze(
        self,
        snapshot: TicketSnapshot,
        *,
        target_resolution: TargetServiceResolution | None = None,
    ) -> tuple[CaseFrame, CaseDecision]:
        deterministic_frame = await self.extractor.extract(snapshot, include_llm=False)
        preliminary = await self.router.decide(
            snapshot,
            deterministic_frame,
            allow_verifier=False,
            target_resolution=target_resolution,
        )
        if preliminary.state in {CaseDecisionState.selected, CaseDecisionState.multi_intent}:
            return deterministic_frame, preliminary
        if preliminary.reason_codes == ["candidate_set_too_broad"]:
            return deterministic_frame, preliminary
        if self.extractor.transport is None:
            return deterministic_frame, preliminary

        enriched_frame = await self.extractor.extract(snapshot, include_llm=True)
        decision = await self.router.decide(
            snapshot,
            enriched_frame,
            target_resolution=target_resolution,
        )
        return enriched_frame, decision


def detect_tense_tone(text: str | None) -> tuple[bool, str | None]:
    if not text or not text.strip():
        return False, None
    for pattern in TENSE_PATTERNS:
        match = pattern.search(text.strip())
        if match:
            return True, f"Обнаружен маркер срочности/напряженности: '{match.group(0)}'"
    return False, None
