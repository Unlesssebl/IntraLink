"""Constrained LLM verifier for ambiguous case-type candidates."""

from __future__ import annotations

import json
import re

from core.automation.case_profiles import CaseProfileRegistry
from core.automation.contracts import CaseCandidate, CaseFrame, TicketSnapshot
from core.automation.frame_extractor import CaseFrameTransport
from core.rag.sanitizer import PIISanitizer

_SECRET_RE = re.compile(r"(?i)\b(password|passwd|пароль|token|secret)\b\s*[:=]?\s*\S+")


class LLMCaseVerifier:
    prompt_version = "case-verifier-v1"

    def __init__(
        self,
        transport: CaseFrameTransport,
        profiles: CaseProfileRegistry,
        *,
        model_alias: str = "helpdesk-reasoning",
    ) -> None:
        self.transport = transport
        self.profiles = profiles
        self.model_alias = model_alias
        self.sanitizer = PIISanitizer(max_length=6000)

    async def verify(
        self,
        snapshot: TicketSnapshot,
        frame: CaseFrame,
        candidates: list[CaseCandidate],
    ) -> tuple[dict[str, str], dict[str, object]]:
        if not 1 <= len(candidates) <= 3:
            raise ValueError("Verifier accepts one to three existing candidates")
        allowed = [candidate.case_type for candidate in candidates]
        payload = {
            "public_text": {
                "title": self._sanitize(snapshot.title),
                "description": self._sanitize(snapshot.description),
                "comments": [self._sanitize(item.text) for item in snapshot.public_comments if not item.is_private],
            },
            "candidates": [
                {
                    "case_type": key,
                    "definition": (self.profiles.get(key).intent_summary if self.profiles.get(key) else key),
                }
                for key in allowed
            ],
            "grounded_assertions": [
                {
                    "kind": assertion.kind.value,
                    "key": assertion.key,
                    "text_span": assertion.text_span,
                    "is_negated": assertion.is_negated,
                }
                for assertion in frame.assertions
                if assertion.text_span
            ],
        }
        raw = await self.transport.complete_json(
            model_alias=self.model_alias,
            system_prompt=(
                "Verify only the supplied case_type candidates against literal public ticket text. "
                "Return JSON {verdicts:[{case_type,verdict,reason_code}]}; verdict must be "
                "supported, contradicted, or insufficient. Never add a candidate or propose an action."
            ),
            payload=payload,
            timeout_seconds=10.0,
        )
        parsed = json.loads(raw)
        rows = parsed.get("verdicts")
        if not isinstance(rows, list):
            raise ValueError("Invalid verifier response")
        verdicts: dict[str, str] = {}
        reasons: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) - {"case_type", "verdict", "reason_code"}:
                raise ValueError("Invalid verifier verdict schema")
            key = str(row.get("case_type", ""))
            verdict = str(row.get("verdict", ""))
            if key not in allowed or key in verdicts:
                raise ValueError("Verifier changed candidate set")
            if verdict not in {"supported", "contradicted", "insufficient"}:
                raise ValueError("Invalid verifier verdict")
            verdicts[key] = verdict
            reasons[key] = str(row.get("reason_code", ""))
        if set(verdicts) != set(allowed):
            raise ValueError("Verifier omitted candidates")
        return verdicts, {"prompt_version": self.prompt_version, "reason_codes": reasons}

    def _sanitize(self, text: str) -> str:
        safe = self.sanitizer.sanitize(text or "").sanitized_text
        return _SECRET_RE.sub("[SECRET]", safe)
