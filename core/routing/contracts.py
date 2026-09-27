"""Pydantic v2 domain contracts for Evidence-Based Routing Cascade.

Defines the immutable contracts and model-level invariants for routing decisions,
evidence tracking, verification verdicts, and snapshot representations.
"""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoutingState(str, Enum):
    selected = "selected"
    needs_clarification = "needs_clarification"
    ambiguous = "ambiguous"
    unmatched = "unmatched"
    degraded = "degraded"


class EvidenceSource(str, Enum):
    service_id = "service_id"
    service_name = "service_name"
    title = "title"
    description = "description"
    comment = "comment"
    custom_field = "custom_field"
    semantic = "semantic"


class EvidencePolarity(str, Enum):
    supports = "supports"
    contradicts = "contradicts"


class EvidenceStrength(str, Enum):
    exact = "exact"
    strong = "strong"
    weak = "weak"


class VerificationVerdict(str, Enum):
    supported = "supported"
    contradicted = "contradicted"
    insufficient = "insufficient"


class FeedbackVerdict(str, Enum):
    confirmed = "confirmed"
    corrected = "corrected"
    rejected = "rejected"
    manual = "manual"


class RoutingBaseModel(BaseModel):
    """Base immutable Pydantic v2 model forbidding extra fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SnapshotComment(RoutingBaseModel):
    """Sanitized public comment entry within a ticket snapshot."""

    id: int | None = None
    text: str
    created_at: str | None = None
    author_name: str
    is_private: bool = False


class SnapshotAttachment(RoutingBaseModel):
    """Metadata-only attachment reference within a ticket snapshot."""

    id: int
    name: str
    size: int


class TicketSnapshot(RoutingBaseModel):
    """Canonical sanitized point-in-time snapshot of an IntraService ticket."""

    task_id: int
    status_id: int
    service_id: int | None = None
    service_name: str | None = None
    title: str
    description: str
    public_comments: list[SnapshotComment] = Field(default_factory=list)
    custom_fields: dict[str, str] = Field(default_factory=dict)
    entities: dict[str, str] = Field(default_factory=dict)
    attachments: list[SnapshotAttachment] = Field(default_factory=list)
    last_event_id: int | None = None
    snapshot_hash: str


class RoutingEvidence(RoutingBaseModel):
    """Atomic piece of evidence supporting or contradicting a candidate scenario."""

    id: str
    candidate_key: str
    source: EvidenceSource
    polarity: EvidencePolarity
    strength: EvidenceStrength
    source_ref: str
    text_span: str | None = None


class ScenarioCandidate(RoutingBaseModel):
    """Scenario candidate proposed by candidate generators with attached evidence."""

    scenario_key: str
    scenario_version: str
    evidence_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)
    sources: set[EvidenceSource] = Field(default_factory=set)


class CandidateVerification(RoutingBaseModel):
    """Outcome of LLM-based verification for grey-zone candidate scenarios."""

    scenario_key: str
    verdict: VerificationVerdict
    evidence_spans: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    referenced_evidence_ids: list[str] = Field(default_factory=list)


class VerifierTrace(RoutingBaseModel):
    """Execution trace metadata for LLM Grey Zone Verifier."""

    status: str
    prompt_version: str
    sanitizer_version: str
    privacy_zone: str
    model_alias: str
    sanitized_input_hash: str
    degradation_reason: str | None = None


class RoutingDecision(RoutingBaseModel):
    """Canonical immutable routing decision record and source of truth."""

    id: UUID = Field(default_factory=uuid4)
    task_id: int
    snapshot_hash: str
    router_version: str
    prompt_version: str | None = None
    state: RoutingState
    selected_scenario: str | None = None
    selected_scenario_version: str | None = None
    candidates: list[ScenarioCandidate] = Field(default_factory=list)
    evidence: list[RoutingEvidence] = Field(default_factory=list)
    verifier_result: list[CandidateVerification] | None = None
    missing_facts: list[str] = Field(default_factory=list)
    degradation_reason: str | None = None
    decision_reason_codes: list[str] = Field(default_factory=list)
    degraded_components: dict[str, str] = Field(default_factory=dict)
    verifier_trace: VerifierTrace | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_invariants(self) -> "RoutingDecision":
        import re

        # 1. selected and needs_clarification require selected_scenario
        if self.state in (RoutingState.selected, RoutingState.needs_clarification) and not self.selected_scenario:
            raise ValueError(
                f"RoutingState '{self.state.value}' requires a non-empty selected_scenario."
            )

        # 2. ambiguous, unmatched, and degraded must not masquerade as having a selected scenario
        if (
            self.state in (RoutingState.ambiguous, RoutingState.unmatched, RoutingState.degraded)
            and self.selected_scenario is not None
        ):
            raise ValueError(
                f"RoutingState '{self.state.value}' must not have a selected_scenario."
            )

        # 3. needs_clarification requires non-empty missing_facts; missing_facts only allowed for needs_clarification
        if self.state == RoutingState.needs_clarification:
            if not self.missing_facts:
                raise ValueError(
                    "RoutingState 'needs_clarification' requires non-empty missing_facts."
                )
        else:
            if self.missing_facts:
                raise ValueError(
                    f"missing_facts is only allowed when state is 'needs_clarification', got '{self.state.value}'."
                )

        # 4. degraded requires degradation_reason; degradation_reason only allowed for degraded
        if self.state == RoutingState.degraded:
            if not self.degradation_reason or not self.degradation_reason.strip():
                raise ValueError(
                    "RoutingState 'degraded' requires a non-empty degradation_reason."
                )
        else:
            if self.degradation_reason is not None:
                raise ValueError(
                    f"degradation_reason is only allowed when state is 'degraded', got '{self.state.value}'."
                )

        # 5. selected_scenario_version is only allowed alongside selected_scenario
        if self.selected_scenario_version is not None and self.selected_scenario is None:
            raise ValueError(
                "selected_scenario_version is only allowed when selected_scenario is present."
            )

        # 5b. selected_scenario_version must match candidate scenario_version
        if self.selected_scenario is not None and self.selected_scenario_version is not None:
            matching_candidate = next((c for c in self.candidates if c.scenario_key == self.selected_scenario), None)
            if matching_candidate is not None and matching_candidate.scenario_version != self.selected_scenario_version:
                raise ValueError(
                    f"selected_scenario_version '{self.selected_scenario_version}' does not match "
                    f"candidate scenario_version '{matching_candidate.scenario_version}' for '{self.selected_scenario}'."
                )

        # 5c. prompt_version and verifier_trace synchronization
        if self.verifier_trace is not None:
            if self.prompt_version != self.verifier_trace.prompt_version:
                raise ValueError(
                    f"prompt_version '{self.prompt_version}' does not match "
                    f"verifier_trace.prompt_version '{self.verifier_trace.prompt_version}'."
                )
            if self.verifier_trace.status == "success":
                if not self.verifier_result:
                    raise ValueError("Successful verifier_trace requires non-empty verifier_result.")
                if self.verifier_trace.degradation_reason is not None:
                    raise ValueError("Successful verifier_trace must not have degradation_reason.")
            elif self.verifier_trace.status == "degraded":
                if self.verifier_result:
                    raise ValueError("Degraded verifier_trace forbids verifier_result.")
                if not self.verifier_trace.degradation_reason or not self.verifier_trace.degradation_reason.strip():
                    raise ValueError("Degraded verifier_trace requires non-empty degradation_reason.")
        else:
            if self.prompt_version is not None and not self.verifier_result:
                raise ValueError("prompt_version is only allowed when verifier was run.")

        # 5d. Machine reason codes format and exception leak prevention
        for code in self.decision_reason_codes:
            if not re.match(r"^[a-z0-9_]+$", code):
                raise ValueError(f"Invalid decision_reason_code '{code}': must be alphanumeric lowercase with underscores.")

        for comp, deg_code in self.degraded_components.items():
            if not re.match(r"^[a-z0-9_]+$", comp):
                raise ValueError(f"Invalid degraded_component key '{comp}': must be alphanumeric lowercase with underscores.")
            if not re.match(r"^[a-z0-9_]+$", deg_code):
                raise ValueError(f"Invalid degraded_component value '{deg_code}': must be alphanumeric lowercase with underscores.")

        for fact in self.missing_facts:
            if not re.match(r"^[a-z0-9_]+$", fact):
                raise ValueError(f"Invalid missing_fact '{fact}': must be alphanumeric lowercase with underscores.")

        forbidden_leak_markers = (
            "traceback", "exception:", "error:", "file \"", "line ",
            "password", "passwd", "пароль", "1489", "field1489",
            "\n", "\r", "{", "}", "<", ">"
        )
        if self.degradation_reason is not None:
            lower_deg = self.degradation_reason.lower()
            if any(marker in lower_deg for marker in forbidden_leak_markers):
                raise ValueError("degradation_reason must not contain exception details, stacktraces, or sensitive payloads.")

        if self.verifier_trace and self.verifier_trace.degradation_reason:
            lower_trace_deg = self.verifier_trace.degradation_reason.lower()
            if any(marker in lower_trace_deg for marker in forbidden_leak_markers):
                raise ValueError("verifier_trace degradation_reason must not contain exception details, stacktraces, or sensitive payloads.")

        # 6. RoutingEvidence.id must be unique across all evidence
        seen_evidence_ids: set[str] = set()
        evidence_by_id: dict[str, RoutingEvidence] = {}
        for ev in self.evidence:
            if ev.id in seen_evidence_ids:
                raise ValueError(f"Duplicate RoutingEvidence.id '{ev.id}' found in evidence list.")
            seen_evidence_ids.add(ev.id)
            evidence_by_id[ev.id] = ev

        # 7. ScenarioCandidate.scenario_key must be unique across candidates
        seen_candidate_keys: set[str] = set()
        for cand in self.candidates:
            if cand.scenario_key in seen_candidate_keys:
                raise ValueError(
                    f"Duplicate ScenarioCandidate.scenario_key '{cand.scenario_key}' found in candidates."
                )
            seen_candidate_keys.add(cand.scenario_key)

        # 8. selected_scenario must be present among candidates if specified
        if self.selected_scenario is not None and self.selected_scenario not in seen_candidate_keys:
            raise ValueError(
                f"selected_scenario '{self.selected_scenario}' must be present among declared candidates."
            )

        # 9. All RoutingEvidence.candidate_key must exist among candidates
        for ev in self.evidence:
            if ev.candidate_key not in seen_candidate_keys:
                raise ValueError(
                    f"RoutingEvidence '{ev.id}' references unknown candidate_key '{ev.candidate_key}'."
                )

        # 10. Candidate evidence links invariants:
        # - evidence_ids and contradiction_ids must reference existing evidence items;
        # - no ID can be in both evidence_ids and contradiction_ids;
        # - evidence_ids must have matching candidate_key and polarity=supports;
        # - contradiction_ids must have matching candidate_key and polarity=contradicts;
        # - candidate.sources must exactly match sources of supporting evidence.
        for candidate in self.candidates:
            overlap = set(candidate.evidence_ids) & set(candidate.contradiction_ids)
            if overlap:
                raise ValueError(
                    f"Candidate '{candidate.scenario_key}' has evidence IDs in both evidence_ids and contradiction_ids: {overlap}."
                )

            supporting_sources: set[EvidenceSource] = set()
            for eid in candidate.evidence_ids:
                if eid not in evidence_by_id:
                    raise ValueError(
                        f"Candidate '{candidate.scenario_key}' references non-existent evidence_id '{eid}'."
                    )
                ev = evidence_by_id[eid]
                if ev.candidate_key != candidate.scenario_key:
                    raise ValueError(
                        f"Candidate '{candidate.scenario_key}' references evidence '{eid}' belonging to candidate '{ev.candidate_key}'."
                    )
                if ev.polarity != EvidencePolarity.supports:
                    raise ValueError(
                        f"Candidate '{candidate.scenario_key}' evidence_ids contains non-supporting evidence '{eid}' with polarity '{ev.polarity.value}'."
                    )
                supporting_sources.add(ev.source)

            for cid in candidate.contradiction_ids:
                if cid not in evidence_by_id:
                    raise ValueError(
                        f"Candidate '{candidate.scenario_key}' references non-existent contradiction_id '{cid}'."
                    )
                ev = evidence_by_id[cid]
                if ev.candidate_key != candidate.scenario_key:
                    raise ValueError(
                        f"Candidate '{candidate.scenario_key}' references contradiction evidence '{cid}' belonging to candidate '{ev.candidate_key}'."
                    )
                if ev.polarity != EvidencePolarity.contradicts:
                    raise ValueError(
                        f"Candidate '{candidate.scenario_key}' contradiction_ids contains non-contradicting evidence '{cid}' with polarity '{ev.polarity.value}'."
                    )

            if set(candidate.sources) != supporting_sources:
                raise ValueError(
                    f"Candidate '{candidate.scenario_key}' sources {candidate.sources} do not match its supporting evidence sources {supporting_sources}."
                )

        # 11. All scenario keys in verifier_result must exist among candidates, be unique,
        # and referenced evidence must be valid supporting evidence belonging to the candidate.
        if self.verifier_result is not None:
            seen_verifier_keys: set[str] = set()
            for ver in self.verifier_result:
                if ver.scenario_key in seen_verifier_keys:
                    raise ValueError(
                        f"Duplicate verifier result for scenario_key '{ver.scenario_key}' in verifier_result."
                    )
                seen_verifier_keys.add(ver.scenario_key)

                if ver.scenario_key not in seen_candidate_keys:
                    raise ValueError(
                        f"CandidateVerification references unknown scenario_key '{ver.scenario_key}'."
                    )

                for ref_eid in ver.referenced_evidence_ids:
                    if ref_eid not in evidence_by_id:
                        raise ValueError(
                            f"CandidateVerification for '{ver.scenario_key}' references non-existent evidence_id '{ref_eid}'."
                        )
                    ev = evidence_by_id[ref_eid]
                    if ev.candidate_key != ver.scenario_key:
                        raise ValueError(
                            f"CandidateVerification for '{ver.scenario_key}' references evidence '{ref_eid}' belonging to candidate '{ev.candidate_key}'."
                        )
                    if ev.polarity != EvidencePolarity.supports:
                        raise ValueError(
                            f"CandidateVerification for '{ver.scenario_key}' references non-supporting evidence '{ref_eid}' with polarity '{ev.polarity.value}'."
                        )

        return self
