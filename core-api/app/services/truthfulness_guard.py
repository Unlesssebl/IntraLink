"""Truthfulness and integrity guards for scenario decisions and command payloads."""

from __future__ import annotations

import re
from typing import Any

from shared.domain import (
    ExecutionPlan,
    LegacyExecutionPlan,
    PlanStep,
    StepKind,
    StepStatus,
)


class TruthfulnessViolation(Exception):
    """Raised when a triage payload or decision violates truthfulness invariants."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


# Lexical patterns for false claims detection
COMPLETION_PATTERNS = [
    re.compile(r"\bуспешно установлен[аоы]?\b", re.IGNORECASE),
    re.compile(r"\bпринтер (?:уже )?установлен\b", re.IGNORECASE),
    re.compile(r"\bпроблема решена\b", re.IGNORECASE),
    re.compile(r"\bдефект устранен\b", re.IGNORECASE),
    re.compile(r"\bошибок не обнаружено\b", re.IGNORECASE),
    re.compile(r"\bнастройка завершена\b", re.IGNORECASE),
    re.compile(r"\bпроверка завершена\b", re.IGNORECASE),
    re.compile(r"\bработает корректно\b", re.IGNORECASE),
    re.compile(r"\bисправлен[оаы]?\b", re.IGNORECASE),
]

EXECUTION_PATTERNS = [
    re.compile(r"\bприступаю к\b", re.IGNORECASE),
    re.compile(r"\bпровожу проверку\b", re.IGNORECASE),
    re.compile(r"\bвыполняется проверка\b", re.IGNORECASE),
    re.compile(r"\bначата диагностика\b", re.IGNORECASE),
]

DISPATCH_PATTERNS = [
    re.compile(r"\bспециалист направлен\b", re.IGNORECASE),
    re.compile(r"\bмастер выехал\b", re.IGNORECASE),
    re.compile(r"\bинженер уже в пути\b", re.IGNORECASE),
    re.compile(r"\bвыезд согласован\b", re.IGNORECASE),
]

USER_INSTRUCTION_PREFIXES = [
    "убедитесь",
    "проверьте",
    "пожалуйста",
    "перезагрузите",
    "включите",
    "сообщите",
    "подключите",
    "уточните",
]


class TruthfulnessGuard:
    """Verifies structural completion and lexical truthfulness of decisions and comments."""

    @staticmethod
    def verify_plan_structural_integrity(
        plan: ExecutionPlan | LegacyExecutionPlan | None,
        target_status_id: int | None,
    ) -> list[str]:
        """Verifies whether target_status_id (especially 29 - Completed) is permitted by plan progress."""
        blocked_reasons: list[str] = []
        if target_status_id != 29:
            return blocked_reasons

        if plan is None:
            return blocked_reasons

        if isinstance(plan, LegacyExecutionPlan):
            # Legacy plans don't have v2 required_for_resolution markers
            return blocked_reasons

        # ExecutionPlan v2: check all required_for_resolution steps
        req_steps = [s for s in plan.steps if s.required_for_resolution]
        for step in req_steps:
            if step.status not in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                blocked_reasons.append(
                    f"unverified_resolution_blocked:step:{step.id}:{step.status.value}"
                )
            elif step.status == StepStatus.SKIPPED and not step.skip_reason:
                blocked_reasons.append(
                    f"unverified_resolution_blocked:step:{step.id}:missing_skip_reason"
                )
            elif step.status == StepStatus.COMPLETED and not step.evidence_refs:
                blocked_reasons.append(
                    f"unverified_resolution_blocked:step:{step.id}:missing_evidence"
                )

        # Check unsupported steps
        unsupported = [s for s in plan.steps if s.status == StepStatus.UNSUPPORTED and s.required_for_resolution]
        for step in unsupported:
            blocked_reasons.append(f"unverified_resolution_blocked:step:{step.id}:unsupported_capability")

        return blocked_reasons

    @staticmethod
    def verify_lexical_truthfulness(
        text: str,
        *,
        phase: str = "collecting",
        evidence_refs: list[str] | None = None,
        target_status_id: int | None = None,
    ) -> list[str]:
        """Analyzes text for unsubstantiated claims according to current execution phase and evidence."""
        violations: list[str] = []
        if not text:
            return violations

        clean_text = text.strip()
        ev_refs = set(evidence_refs or [])

        # Split into sentences to distinguish instructions to user from service claims
        sentences = re.split(r"[.!?\n]+", clean_text)

        has_terminal_evidence = any(
            ":succeeded" in ref or ":completed" in ref or ":verified" in ref
            for ref in ev_refs
        )
        has_running_evidence = any(
            ":running" in ref or ":dispatched" in ref or ":started" in ref
            for ref in ev_refs
        )
        has_dispatch_evidence = any(
            "service_assigned" in ref or "engineer_dispatched" in ref
            for ref in ev_refs
        )

        for sentence in sentences:
            sentence_clean = sentence.strip()
            if not sentence_clean:
                continue

            words = sentence_clean.split()
            first_word = words[0].lower() if words else ""
            if any(first_word.startswith(prefix) for prefix in USER_INSTRUCTION_PREFIXES):
                # Instruction to user, not a service claim
                continue

            # Check completion claims
            if any(pattern.search(sentence_clean) for pattern in COMPLETION_PATTERNS):
                if phase not in ("completed", "verifying") or not has_terminal_evidence:
                    violations.append(f"unverified_claim_completion:{sentence_clean[:40]}")

            # Check running claims
            if any(pattern.search(sentence_clean) for pattern in EXECUTION_PATTERNS):
                if phase not in ("running", "dispatched", "verifying", "completed") and not has_running_evidence:
                    violations.append(f"unverified_claim_execution:{sentence_clean[:40]}")

            # Check dispatch claims
            if any(pattern.search(sentence_clean) for pattern in DISPATCH_PATTERNS):
                if not has_dispatch_evidence:
                    violations.append(f"unverified_claim_dispatch:{sentence_clean[:40]}")

        return violations

    @staticmethod
    def validate_apply_triage_payload(
        *,
        status_id: int,
        comment: str,
        is_private: bool = False,
        execution_plan: ExecutionPlan | LegacyExecutionPlan | None = None,
        evidence_refs: list[str] | None = None,
    ) -> None:
        """Enforces truthfulness invariants at the command boundary before creating apply_triage records."""
        # Invariant 1: Private comments must never resolve a ticket with status 29
        if is_private and status_id == 29:
            raise TruthfulnessViolation(
                "private_resolution_forbidden",
                "Внутренний комментарий не может закрывать заявку со статусом 29. "
                "Для закрытия заявки требуется публичный ответ заявителю.",
            )

        # Invariant 2: Status 29 requires all mandatory plan steps to be completed
        if status_id == 29:
            structural_violations = TruthfulnessGuard.verify_plan_structural_integrity(
                execution_plan, target_status_id=status_id
            )
            if structural_violations:
                raise TruthfulnessViolation(
                    "unverified_resolution_blocked",
                    f"Перевод в статус 29 заблокирован: не завершены обязательные шаги плана: {', '.join(structural_violations)}",
                    details={"violations": structural_violations},
                )

        # Invariant 3: Lexical guard on comment text
        phase = getattr(execution_plan, "phase", "completed" if status_id == 29 else "ready")
        lexical_violations = TruthfulnessGuard.verify_lexical_truthfulness(
            comment,
            phase=phase,
            evidence_refs=evidence_refs,
            target_status_id=status_id,
        )
        if lexical_violations:
            raise TruthfulnessViolation(
                "unverified_text_claims",
                f"Текст комментария содержит неподтвержденные утверждения: {', '.join(lexical_violations)}",
                details={"violations": lexical_violations},
            )
