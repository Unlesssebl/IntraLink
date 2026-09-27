"""Unit tests for GreyZonePolicy and DecisionPolicy in Evidence-Based Routing Cascade."""

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
from core.routing.decision_policy import DecisionPolicy, GreyZonePolicy


def _make_ev(
    ev_id: str,
    candidate_key: str,
    source: EvidenceSource,
    strength: EvidenceStrength = EvidenceStrength.strong,
    polarity: EvidencePolarity = EvidencePolarity.supports,
) -> RoutingEvidence:
    return RoutingEvidence(
        id=ev_id,
        candidate_key=candidate_key,
        source=source,
        strength=strength,
        polarity=polarity,
        source_ref="test",
    )


def test_direct_selection_by_exact_service_id():
    policy = GreyZonePolicy()
    ev1 = _make_ev("ev-1", "grant_wlan", EvidenceSource.service_id, strength=EvidenceStrength.exact)
    cand1 = ScenarioCandidate(
        scenario_key="grant_wlan",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_id},
    )

    outcome = policy.evaluate(candidates=[cand1], evidence=[ev1], degraded_providers={})
    assert outcome.direct_winner is not None
    assert outcome.direct_winner.candidate.scenario_key == "grant_wlan"
    assert outcome.direct_winner.reason_code == "direct_exact_service_id"


def test_direct_selection_by_two_non_semantic_sources():
    policy = GreyZonePolicy()
    ev1 = _make_ev("ev-1", "grant_wlan", EvidenceSource.service_name, strength=EvidenceStrength.strong)
    ev2 = _make_ev("ev-2", "grant_wlan", EvidenceSource.title, strength=EvidenceStrength.strong)
    cand1 = ScenarioCandidate(
        scenario_key="grant_wlan",
        scenario_version="1.0.0",
        evidence_ids=["ev-1", "ev-2"],
        sources={EvidenceSource.service_name, EvidenceSource.title},
    )

    outcome = policy.evaluate(candidates=[cand1], evidence=[ev1, ev2], degraded_providers={})
    assert outcome.direct_winner is not None
    assert outcome.direct_winner.candidate.scenario_key == "grant_wlan"
    assert outcome.direct_winner.reason_code == "direct_multi_source_evidence"


def test_consultation_only_never_selected_directly():
    policy = GreyZonePolicy()
    ev1 = _make_ev("ev-1", "rag_consultation", EvidenceSource.service_id, strength=EvidenceStrength.exact)
    cand1 = ScenarioCandidate(
        scenario_key="rag_consultation",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_id},
    )

    outcome = policy.evaluate(candidates=[cand1], evidence=[ev1], degraded_providers={})
    assert outcome.direct_winner is None
    # Eligible for verifier, not chosen directly
    assert len(outcome.eligible_candidates) == 1
    assert outcome.eligible_candidates[0].scenario_key == "rag_consultation"


def test_weak_contradiction_moves_to_grey_zone():
    policy = GreyZonePolicy()
    ev_sup = _make_ev("ev-1", "install_printer", EvidenceSource.service_id, strength=EvidenceStrength.exact)
    ev_contra = _make_ev(
        "ev-contra",
        "install_printer",
        EvidenceSource.description,
        strength=EvidenceStrength.weak,
        polarity=EvidencePolarity.contradicts,
    )
    cand = ScenarioCandidate(
        scenario_key="install_printer",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        contradiction_ids=["ev-contra"],
        sources={EvidenceSource.service_id},
    )

    outcome = policy.evaluate(candidates=[cand], evidence=[ev_sup, ev_contra], degraded_providers={})
    assert outcome.direct_winner is None
    # Candidate remains eligible for verifier, but direct selection is denied
    assert len(outcome.eligible_candidates) == 1


def test_strong_or_exact_contradiction_disqualifies_candidate():
    policy = GreyZonePolicy()
    ev_sup = _make_ev("ev-1", "install_printer", EvidenceSource.service_id, strength=EvidenceStrength.exact)
    ev_contra = _make_ev(
        "ev-contra",
        "install_printer",
        EvidenceSource.description,
        strength=EvidenceStrength.strong,
        polarity=EvidencePolarity.contradicts,
    )
    cand = ScenarioCandidate(
        scenario_key="install_printer",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        contradiction_ids=["ev-contra"],
        sources={EvidenceSource.service_id},
    )

    outcome = policy.evaluate(candidates=[cand], evidence=[ev_sup, ev_contra], degraded_providers={})
    assert outcome.direct_winner is None
    assert len(outcome.eligible_candidates) == 0
    assert outcome.terminal_state == RoutingState.unmatched


def test_common_printer_service_id_does_not_prematurely_select():
    """Service 19 triggers install_printer, printer_spooler_restart, default_printer_fix.

    Neither candidate dominates alone, so direct selection must be denied.
    """
    policy = GreyZonePolicy()
    ev1 = _make_ev("ev-1", "install_printer", EvidenceSource.service_id, strength=EvidenceStrength.exact)
    ev2 = _make_ev("ev-2", "printer_spooler_restart", EvidenceSource.service_id, strength=EvidenceStrength.exact)
    ev3 = _make_ev("ev-3", "default_printer_fix", EvidenceSource.service_id, strength=EvidenceStrength.exact)

    cand1 = ScenarioCandidate(
        scenario_key="install_printer",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_id},
    )
    cand2 = ScenarioCandidate(
        scenario_key="printer_spooler_restart",
        scenario_version="1.0.0",
        evidence_ids=["ev-2"],
        sources={EvidenceSource.service_id},
    )
    cand3 = ScenarioCandidate(
        scenario_key="default_printer_fix",
        scenario_version="1.0.0",
        evidence_ids=["ev-3"],
        sources={EvidenceSource.service_id},
    )

    outcome = policy.evaluate(
        candidates=[cand1, cand2, cand3],
        evidence=[ev1, ev2, ev3],
        degraded_providers={},
    )
    assert outcome.direct_winner is None
    assert len(outcome.eligible_candidates) == 3


def test_semantic_only_candidate_never_selected_directly():
    policy = GreyZonePolicy()
    ev = _make_ev("ev-1", "install_printer", EvidenceSource.semantic, strength=EvidenceStrength.strong)
    cand = ScenarioCandidate(
        scenario_key="install_printer",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.semantic},
    )

    outcome = policy.evaluate(candidates=[cand], evidence=[ev], degraded_providers={})
    assert outcome.direct_winner is None
    assert len(outcome.eligible_candidates) == 1


def test_partial_provider_degradation_does_not_block_proven_choice():
    """If semantic provider timed out, but exact service_id is proven, direct selection wins."""
    policy = GreyZonePolicy()
    ev = _make_ev("ev-1", "account_lock", EvidenceSource.service_id, strength=EvidenceStrength.exact)
    cand = ScenarioCandidate(
        scenario_key="account_lock",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_id},
    )

    outcome = policy.evaluate(
        candidates=[cand],
        evidence=[ev],
        degraded_providers={"semantic": "provider_timeout"},
    )
    assert outcome.direct_winner is not None
    assert outcome.direct_winner.candidate.scenario_key == "account_lock"


def test_broad_candidate_set_results_in_ambiguous():
    policy = GreyZonePolicy()
    cands = [
        ScenarioCandidate(
            scenario_key=f"scen_{i}",
            scenario_version="1.0.0",
            evidence_ids=[f"ev-{i}"],
            sources={EvidenceSource.title},
        )
        for i in range(4)
    ]
    evs = [_make_ev(f"ev-{i}", f"scen_{i}", EvidenceSource.title) for i in range(4)]

    outcome = policy.evaluate(candidates=cands, evidence=evs, degraded_providers={})
    assert outcome.direct_winner is None
    assert outcome.terminal_state == RoutingState.ambiguous
    assert outcome.terminal_reason_codes == ["candidate_set_too_broad"]


def test_no_candidates_with_degraded_provider_results_in_degraded():
    policy = GreyZonePolicy()
    outcome = policy.evaluate(candidates=[], evidence=[], degraded_providers={"semantic": "provider_timeout"})
    assert outcome.terminal_state == RoutingState.degraded
    assert outcome.terminal_degradation_reason == "candidate_generation_degraded"
    assert outcome.terminal_reason_codes == ["candidate_generation_degraded"]


def test_no_candidates_healthy_providers_results_in_unmatched():
    policy = GreyZonePolicy()
    outcome = policy.evaluate(candidates=[], evidence=[], degraded_providers={})
    assert outcome.terminal_state == RoutingState.unmatched
    assert outcome.terminal_reason_codes == ["no_candidates_found"]


def test_decision_policy_arbitrate_verifier_results():
    dec_policy = DecisionPolicy()
    cand1 = ScenarioCandidate(scenario_key="grant_wlan", scenario_version="1.0.0")
    cand2 = ScenarioCandidate(scenario_key="install_printer", scenario_version="1.0.0")

    trace_success = VerifierTrace(
        status="success",
        prompt_version="verifier-v1",
        sanitizer_version="dlp-v1",
        privacy_zone="red",
        model_alias="helpdesk-local",
        sanitized_input_hash="1" * 64,
    )

    # 1. Exactly one supported -> selected
    ver1 = CandidateVerification(scenario_key="grant_wlan", verdict=VerificationVerdict.supported)
    ver2 = CandidateVerification(scenario_key="install_printer", verdict=VerificationVerdict.contradicted)
    res_single = dec_policy.arbitrate_verifier_result([ver1, ver2], trace_success, [cand1, cand2])
    assert res_single.state == RoutingState.selected
    assert res_single.selected_candidate is not None
    assert res_single.selected_candidate.scenario_key == "grant_wlan"

    # 2. Multiple supported -> ambiguous
    ver1_sup = CandidateVerification(scenario_key="grant_wlan", verdict=VerificationVerdict.supported)
    ver2_sup = CandidateVerification(scenario_key="install_printer", verdict=VerificationVerdict.supported)
    res_multi = dec_policy.arbitrate_verifier_result([ver1_sup, ver2_sup], trace_success, [cand1, cand2])
    assert res_multi.state == RoutingState.ambiguous
    assert "multiple_supported_candidates" in res_multi.reason_codes

    # 3. Zero supported, at least one insufficient -> ambiguous
    ver1_ins = CandidateVerification(scenario_key="grant_wlan", verdict=VerificationVerdict.insufficient)
    ver2_con = CandidateVerification(scenario_key="install_printer", verdict=VerificationVerdict.contradicted)
    res_ins = dec_policy.arbitrate_verifier_result([ver1_ins, ver2_con], trace_success, [cand1, cand2])
    assert res_ins.state == RoutingState.ambiguous
    assert "insufficient_evidence" in res_ins.reason_codes

    # 4. All contradicted -> unmatched
    ver1_con = CandidateVerification(scenario_key="grant_wlan", verdict=VerificationVerdict.contradicted)
    res_all_con = dec_policy.arbitrate_verifier_result([ver1_con, ver2_con], trace_success, [cand1, cand2])
    assert res_all_con.state == RoutingState.unmatched
    assert "all_candidates_contradicted" in res_all_con.reason_codes

    # 5. Verifier degraded trace -> degraded
    trace_deg = VerifierTrace(
        status="degraded",
        prompt_version="verifier-v1",
        sanitizer_version="dlp-v1",
        privacy_zone="red",
        model_alias="helpdesk-local",
        sanitized_input_hash="1" * 64,
        degradation_reason="verifier_timeout",
    )
    res_deg = dec_policy.arbitrate_verifier_result([], trace_deg, [cand1, cand2])
    assert res_deg.state == RoutingState.degraded
    assert res_deg.degradation_reason == "verifier_timeout"
