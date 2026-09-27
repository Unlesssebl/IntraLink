"""Pydantic v2 contracts and invariants for LLM Grey Zone Verifier.

Defines privacy zones, degradation codes, run statuses, and structured output schemas
with strict extra-field forbidding and immutable configurations.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.routing.contracts import CandidateVerification, VerificationVerdict

ALLOWED_DEGRADATION_CODES = frozenset({
    "verifier_timeout",
    "verifier_unavailable",
    "verifier_invalid_json",
    "verifier_invalid_schema",
    "verifier_candidate_mismatch",
    "verifier_invalid_evidence",
    "verifier_policy_blocked",
})


class PrivacyZone(str, Enum):
    red = "red"
    yellow = "yellow"
    green = "green"


class VerifierRunStatus(str, Enum):
    success = "success"
    degraded = "degraded"


class VerifierBaseModel(BaseModel):
    """Base immutable Pydantic v2 model forbidding extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RawCandidateVerificationOutput(VerifierBaseModel):
    """Strict schema for individual candidate verification output from LLM."""

    scenario_key: str
    verdict: VerificationVerdict
    evidence_spans: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    referenced_evidence_ids: List[str] = Field(default_factory=list)


class RawVerifierOutput(VerifierBaseModel):
    """Strict top-level structured output schema expected from LLM."""

    verifications: List[RawCandidateVerificationOutput]


class VerifierRunResult(VerifierBaseModel):
    """Canonical immutable result of an LLM Grey Zone Verifier execution."""

    status: VerifierRunStatus
    verifications: List[CandidateVerification] = Field(default_factory=list)
    prompt_version: str
    sanitizer_version: str
    requested_model_alias: str
    privacy_zone: PrivacyZone
    sanitized_input_hash: str
    degradation_reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_invariants(self) -> "VerifierRunResult":
        if self.status == VerifierRunStatus.success:
            if not self.verifications:
                raise ValueError("VerifierRunResult with status 'success' requires non-empty verifications.")
            if self.degradation_reason is not None:
                raise ValueError("VerifierRunResult with status 'success' must not have degradation_reason.")

        elif self.status == VerifierRunStatus.degraded:
            if self.verifications:
                raise ValueError("VerifierRunResult with status 'degraded' requires empty verifications.")
            if not self.degradation_reason or not self.degradation_reason.strip():
                raise ValueError("VerifierRunResult with status 'degraded' requires non-empty degradation_reason.")
            if self.degradation_reason not in ALLOWED_DEGRADATION_CODES:
                raise ValueError(
                    f"Invalid degradation_reason '{self.degradation_reason}'. Must be one of: {sorted(ALLOWED_DEGRADATION_CODES)}"
                )

        return self
