"""Unit tests for Evidence-Based Routing Cascade domain contracts and invariants."""

import pytest
from pydantic import ValidationError

from core.routing.contracts import (
    CandidateVerification,
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingDecision,
    RoutingEvidence,
    RoutingState,
    ScenarioCandidate,
    VerificationVerdict,
)


def _make_sample_evidence(
    ev_id: str = "ev-1",
    candidate_key: str = "install_printer",
) -> RoutingEvidence:
    return RoutingEvidence(
        id=ev_id,
        candidate_key=candidate_key,
        source=EvidenceSource.title,
        polarity=EvidencePolarity.supports,
        strength=EvidenceStrength.strong,
        source_ref="title",
        text_span="установить принтер",
    )


def _make_sample_candidate(
    scenario_key: str = "install_printer",
    scenario_version: str = "1.0.0",
    evidence_ids: list[str] | None = None,
    contradiction_ids: list[str] | None = None,
    sources: set[EvidenceSource] | None = None,
) -> ScenarioCandidate:
    ev_ids = ["ev-1"] if evidence_ids is None else evidence_ids
    con_ids = [] if contradiction_ids is None else contradiction_ids
    srcs = {EvidenceSource.title} if sources is None else sources
    return ScenarioCandidate(
        scenario_key=scenario_key,
        scenario_version=scenario_version,
        evidence_ids=ev_ids,
        contradiction_ids=con_ids,
        sources=srcs,
    )


def test_valid_selected_decision():
    """Verify valid selected routing decision with candidates and evidence."""
    ev = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    cand = _make_sample_candidate(scenario_key="install_printer", evidence_ids=["ev-1"])

    decision = RoutingDecision(
        task_id=1001,
        snapshot_hash="a" * 64,
        router_version="2.0.0",
        state=RoutingState.selected,
        selected_scenario="install_printer",
        selected_scenario_version="1.0.0",
        candidates=[cand],
        evidence=[ev],
    )
    assert decision.state == RoutingState.selected
    assert decision.selected_scenario == "install_printer"
    assert decision.selected_scenario_version == "1.0.0"
    assert len(decision.candidates) == 1
    assert len(decision.evidence) == 1
    assert decision.prompt_version is None


def test_forbidden_selected_without_scenario():
    """RoutingState.selected requires a non-empty selected_scenario."""
    with pytest.raises(ValidationError, match="requires a non-empty selected_scenario"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario=None,
        )


def test_needs_clarification_without_scenario():
    """RoutingState.needs_clarification requires a selected_scenario."""
    with pytest.raises(ValidationError, match="requires a non-empty selected_scenario"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.needs_clarification,
            selected_scenario=None,
            missing_facts=["target_pc"],
        )


def test_needs_clarification_without_missing_facts():
    """RoutingState.needs_clarification requires non-empty missing_facts."""
    with pytest.raises(ValidationError, match="requires non-empty missing_facts"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.needs_clarification,
            selected_scenario="install_printer",
            missing_facts=[],
        )


def test_degraded_without_reason():
    """RoutingState.degraded requires non-empty degradation_reason."""
    with pytest.raises(ValidationError, match="requires a non-empty degradation_reason"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.degraded,
            degradation_reason=None,
        )

    # Empty string should also be rejected
    with pytest.raises(ValidationError, match="requires a non-empty degradation_reason"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.degraded,
            degradation_reason="   ",
        )


def test_ambiguous_or_unmatched_must_not_have_selected_scenario():
    """Ambiguous, unmatched, and degraded must not pretend to have a selected scenario."""
    with pytest.raises(ValidationError, match="must not have a selected_scenario"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.ambiguous,
            selected_scenario="grant_wlan",
        )

    with pytest.raises(ValidationError, match="must not have a selected_scenario"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.unmatched,
            selected_scenario="grant_wlan",
        )

    with pytest.raises(ValidationError, match="must not have a selected_scenario"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.degraded,
            selected_scenario="grant_wlan",
            degradation_reason="LiteLLM Gateway down",
        )


def test_selected_scenario_version_without_selected_scenario():
    """selected_scenario_version is only allowed alongside selected_scenario."""
    with pytest.raises(ValidationError, match="only allowed when selected_scenario is present"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.unmatched,
            selected_scenario=None,
            selected_scenario_version="1.0.0",
        )


def test_candidate_references_non_existent_evidence():
    """Candidate evidence_ids must reference declared evidence."""
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(evidence_ids=["ev-non-existent"])

    with pytest.raises(ValidationError, match="references non-existent evidence_id 'ev-non-existent'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
        )


def test_candidate_references_non_existent_contradiction():
    """Candidate contradiction_ids must reference declared evidence."""
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(evidence_ids=["ev-1"], contradiction_ids=["ev-contra-missing"])

    with pytest.raises(ValidationError, match="references non-existent contradiction_id 'ev-contra-missing'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
        )


def test_verifier_references_unknown_scenario():
    """CandidateVerification scenario_key must exist among candidates."""
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(scenario_key="install_printer", evidence_ids=["ev-1"])
    ver = CandidateVerification(
        scenario_key="unknown_scenario",
        verdict=VerificationVerdict.supported,
    )

    with pytest.raises(ValidationError, match="references unknown scenario_key 'unknown_scenario'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
            verifier_result=[ver],
        )


def test_extra_fields_forbidden():
    """Extra unexpected fields are strictly forbidden across routing models."""
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs are not permitted"):
        RoutingEvidence(
            id="ev-1",
            candidate_key="test",
            source=EvidenceSource.title,
            polarity=EvidencePolarity.supports,
            strength=EvidenceStrength.strong,
            source_ref="title",
            unexpected_field="hack",
        )

    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs are not permitted"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.unmatched,
            rogue_field="injection",
        )


def test_immutability_frozen_models():
    """Models are immutable and reject in-place attribute mutations."""
    ev = _make_sample_evidence(ev_id="ev-1")
    with pytest.raises(ValidationError, match="Instance is frozen|is frozen"):
        ev.strength = EvidenceStrength.weak


def test_duplicate_evidence_id_rejected():
    """Duplicate RoutingEvidence.id must be rejected."""
    ev1 = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    ev2 = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    cand = _make_sample_candidate(scenario_key="install_printer", evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="Duplicate RoutingEvidence.id 'ev-1'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev1, ev2],
        )


def test_duplicate_candidate_key_rejected():
    """Duplicate ScenarioCandidate.scenario_key must be rejected."""
    ev1 = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    cand1 = _make_sample_candidate(scenario_key="install_printer", evidence_ids=["ev-1"])
    cand2 = _make_sample_candidate(scenario_key="install_printer", evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="Duplicate ScenarioCandidate.scenario_key 'install_printer'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand1, cand2],
            evidence=[ev1],
        )


def test_selected_scenario_not_in_candidates_rejected():
    """selected_scenario must exist among candidates."""
    ev = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    cand = _make_sample_candidate(scenario_key="install_printer", evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="selected_scenario 'grant_wlan' must be present among declared candidates"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="grant_wlan",
            candidates=[cand],
            evidence=[ev],
        )


def test_evidence_candidate_key_not_in_candidates_rejected():
    """RoutingEvidence.candidate_key must exist among candidates."""
    ev = _make_sample_evidence(ev_id="ev-1", candidate_key="unknown_scenario")
    cand = _make_sample_candidate(scenario_key="install_printer", evidence_ids=[])

    with pytest.raises(ValidationError, match="RoutingEvidence 'ev-1' references unknown candidate_key 'unknown_scenario'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.unmatched,
            candidates=[cand],
            evidence=[ev],
        )


def test_evidence_ids_wrong_candidate_key_rejected():
    """Candidate evidence_ids cannot reference evidence for another scenario."""
    ev_wlan = _make_sample_evidence(ev_id="ev-wlan", candidate_key="grant_wlan")
    cand_printer = _make_sample_candidate(
        scenario_key="install_printer",
        evidence_ids=["ev-wlan"],
        sources={EvidenceSource.title},
    )
    cand_wlan = _make_sample_candidate(scenario_key="grant_wlan", evidence_ids=[], sources=set())

    with pytest.raises(ValidationError, match="Candidate 'install_printer' references evidence 'ev-wlan' belonging to candidate 'grant_wlan'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.unmatched,
            candidates=[cand_printer, cand_wlan],
            evidence=[ev_wlan],
        )


def test_evidence_ids_contradicting_polarity_rejected():
    """Candidate evidence_ids must only have polarity=supports."""
    ev = RoutingEvidence(
        id="ev-1",
        candidate_key="install_printer",
        source=EvidenceSource.title,
        polarity=EvidencePolarity.contradicts,
        strength=EvidenceStrength.strong,
        source_ref="title",
    )
    cand = _make_sample_candidate(
        scenario_key="install_printer",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.title},
    )

    with pytest.raises(ValidationError, match="evidence_ids contains non-supporting evidence 'ev-1' with polarity 'contradicts'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.unmatched,
            candidates=[cand],
            evidence=[ev],
        )


def test_contradiction_ids_supporting_polarity_rejected():
    """Candidate contradiction_ids must only have polarity=contradicts."""
    ev = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    cand = _make_sample_candidate(
        scenario_key="install_printer",
        evidence_ids=[],
        contradiction_ids=["ev-1"],
        sources=set(),
    )

    with pytest.raises(ValidationError, match="contradiction_ids contains non-contradicting evidence 'ev-1' with polarity 'supports'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.unmatched,
            candidates=[cand],
            evidence=[ev],
        )


def test_evidence_in_both_supporting_and_contradiction_rejected():
    """An evidence ID cannot appear in both evidence_ids and contradiction_ids."""
    ev = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    cand = _make_sample_candidate(
        scenario_key="install_printer",
        evidence_ids=["ev-1"],
        contradiction_ids=["ev-1"],
        sources={EvidenceSource.title},
    )

    with pytest.raises(ValidationError, match="has evidence IDs in both evidence_ids and contradiction_ids"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.unmatched,
            candidates=[cand],
            evidence=[ev],
        )


def test_candidate_sources_mismatch_rejected():
    """Candidate sources must strictly match the sources of its supporting evidence."""
    ev = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    cand = _make_sample_candidate(
        scenario_key="install_printer",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.title, EvidenceSource.service_id},  # service_id not in evidence!
    )

    with pytest.raises(ValidationError, match="do not match its supporting evidence sources"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
        )


def test_verifier_result_duplicate_scenario_key_rejected():
    """Duplicate scenario_key in verifier_result must be rejected."""
    ev = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    cand = _make_sample_candidate(scenario_key="install_printer", evidence_ids=["ev-1"])
    ver1 = CandidateVerification(
        scenario_key="install_printer",
        verdict=VerificationVerdict.supported,
    )
    ver2 = CandidateVerification(
        scenario_key="install_printer",
        verdict=VerificationVerdict.insufficient,
    )

    with pytest.raises(ValidationError, match="Duplicate verifier result for scenario_key 'install_printer'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
            verifier_result=[ver1, ver2],
        )


def test_verifier_result_referenced_evidence_invariants():
    """Verify referenced_evidence_ids validation in verifier_result."""
    ev1 = _make_sample_evidence(ev_id="ev-1", candidate_key="install_printer")
    ev2 = RoutingEvidence(
        id="ev-2",
        candidate_key="grant_wlan",
        source=EvidenceSource.title,
        polarity=EvidencePolarity.supports,
        strength=EvidenceStrength.strong,
        source_ref="title",
    )
    ev_contra = RoutingEvidence(
        id="ev-contra",
        candidate_key="install_printer",
        source=EvidenceSource.description,
        polarity=EvidencePolarity.contradicts,
        strength=EvidenceStrength.strong,
        source_ref="description",
    )

    cand_printer = _make_sample_candidate(
        scenario_key="install_printer",
        evidence_ids=["ev-1"],
        contradiction_ids=["ev-contra"],
    )
    cand_wlan = ScenarioCandidate(
        scenario_key="grant_wlan",
        scenario_version="1.0.0",
        evidence_ids=["ev-2"],
        contradiction_ids=[],
        sources={EvidenceSource.title},
    )

    # 1. Non-existent evidence ID in verifier_result
    ver_missing = CandidateVerification(
        scenario_key="install_printer",
        verdict=VerificationVerdict.supported,
        referenced_evidence_ids=["ev-non-existent"],
    )
    with pytest.raises(ValidationError, match="references non-existent evidence_id 'ev-non-existent'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand_printer, cand_wlan],
            evidence=[ev1, ev2, ev_contra],
            verifier_result=[ver_missing],
        )

    # 2. Evidence ID belonging to a different candidate
    ver_cross = CandidateVerification(
        scenario_key="install_printer",
        verdict=VerificationVerdict.supported,
        referenced_evidence_ids=["ev-2"],  # ev-2 belongs to grant_wlan!
    )
    with pytest.raises(ValidationError, match="belonging to candidate 'grant_wlan'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand_printer, cand_wlan],
            evidence=[ev1, ev2, ev_contra],
            verifier_result=[ver_cross],
        )

    # 3. Evidence ID with non-supporting polarity (contradicts)
    ver_non_supporting = CandidateVerification(
        scenario_key="install_printer",
        verdict=VerificationVerdict.contradicted,
        referenced_evidence_ids=["ev-contra"],
    )
    with pytest.raises(ValidationError, match="with polarity 'contradicts'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand_printer, cand_wlan],
            evidence=[ev1, ev2, ev_contra],
            verifier_result=[ver_non_supporting],
        )

    # 4. Valid referenced evidence for supported and empty for insufficient
    ver_valid_sup = CandidateVerification(
        scenario_key="install_printer",
        verdict=VerificationVerdict.supported,
        evidence_spans=["установить принтер"],
        referenced_evidence_ids=["ev-1"],
    )
    ver_valid_ins = CandidateVerification(
        scenario_key="grant_wlan",
        verdict=VerificationVerdict.insufficient,
        missing_information=["не указан логин"],
        referenced_evidence_ids=[],
    )
    valid_dec = RoutingDecision(
        task_id=1001,
        snapshot_hash="a" * 64,
        router_version="2.0.0",
        state=RoutingState.selected,
        selected_scenario="install_printer",
        candidates=[cand_printer, cand_wlan],
        evidence=[ev1, ev2, ev_contra],
        verifier_result=[ver_valid_sup, ver_valid_ins],
    )
    assert valid_dec.verifier_result is not None
    assert len(valid_dec.verifier_result) == 2


def test_degradation_reason_forbidden_when_not_degraded():
    """degradation_reason is only allowed for state=degraded."""
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="degradation_reason is only allowed when state is 'degraded'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
            degradation_reason="some_reason",
        )


def test_missing_facts_forbidden_when_not_needs_clarification():
    """missing_facts is only allowed for state=needs_clarification."""
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="missing_facts is only allowed when state is 'needs_clarification'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
            missing_facts=["pc_name"],
        )


def test_prompt_version_must_match_verifier_trace():
    """prompt_version must match verifier_trace.prompt_version."""
    from core.routing.contracts import VerifierTrace

    trace = VerifierTrace(
        status="success",
        prompt_version="candidate-verifier-v1",
        sanitizer_version="dlp-verifier-v1",
        privacy_zone="green",
        model_alias="helpdesk-fast",
        sanitized_input_hash="h" * 64,
    )
    ver = CandidateVerification(scenario_key="install_printer", verdict=VerificationVerdict.supported)
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="prompt_version 'mismatched-v2' does not match"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            prompt_version="mismatched-v2",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
            verifier_result=[ver],
            verifier_trace=trace,
        )


def test_successful_verifier_trace_requires_verifier_result():
    """Successful verifier trace requires non-empty verifier_result."""
    from core.routing.contracts import VerifierTrace

    trace = VerifierTrace(
        status="success",
        prompt_version="candidate-verifier-v1",
        sanitizer_version="dlp-verifier-v1",
        privacy_zone="green",
        model_alias="helpdesk-fast",
        sanitized_input_hash="h" * 64,
    )
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="Successful verifier_trace requires non-empty verifier_result"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            prompt_version="candidate-verifier-v1",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
            verifier_result=None,
            verifier_trace=trace,
        )


def test_degraded_verifier_trace_forbids_verifier_result():
    """Degraded verifier trace forbids verifier_result."""
    from core.routing.contracts import VerifierTrace

    trace = VerifierTrace(
        status="degraded",
        prompt_version="candidate-verifier-v1",
        sanitizer_version="dlp-verifier-v1",
        privacy_zone="green",
        model_alias="helpdesk-fast",
        sanitized_input_hash="h" * 64,
        degradation_reason="verifier_timeout",
    )
    ver = CandidateVerification(scenario_key="install_printer", verdict=VerificationVerdict.supported)
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="Degraded verifier_trace forbids verifier_result"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            prompt_version="candidate-verifier-v1",
            state=RoutingState.degraded,
            degradation_reason="verifier_timeout",
            candidates=[cand],
            evidence=[ev],
            verifier_result=[ver],
            verifier_trace=trace,
        )


def test_selected_scenario_version_must_match_candidate():
    """selected_scenario_version must match candidate scenario_version."""
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(scenario_key="install_printer", scenario_version="1.0.0", evidence_ids=["ev-1"])

    with pytest.raises(ValidationError, match="does not match candidate scenario_version '1.0.0'"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            selected_scenario_version="2.0.0",
            candidates=[cand],
            evidence=[ev],
        )


def test_reason_codes_and_degradation_leak_prevention():
    """Reason codes must be valid machine codes and degradation reasons must not leak exceptions or secrets."""
    ev = _make_sample_evidence(ev_id="ev-1")
    cand = _make_sample_candidate(evidence_ids=["ev-1"])

    # Invalid characters in decision_reason_codes
    with pytest.raises(ValidationError, match="Invalid decision_reason_code"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.selected,
            selected_scenario="install_printer",
            candidates=[cand],
            evidence=[ev],
            decision_reason_codes=["bad code with spaces!"],
        )

    # Leak in degradation_reason
    with pytest.raises(ValidationError, match="degradation_reason must not contain exception details"):
        RoutingDecision(
            task_id=1001,
            snapshot_hash="a" * 64,
            router_version="2.0.0",
            state=RoutingState.degraded,
            degradation_reason="Traceback (most recent call last):\n  File 'x.py', line 10",
            candidates=[cand],
            evidence=[ev],
        )



