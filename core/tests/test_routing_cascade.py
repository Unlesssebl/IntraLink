import json
from collections.abc import Sequence

import pytest

from core.routing.candidate_generator import (
    CandidateGenerationResult,
    CandidateGenerator,
)
from core.routing.cascade import ROUTER_VERSION, RoutingCascade
from core.routing.contracts import (
    CandidateVerification,
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingEvidence,
    RoutingState,
    ScenarioCandidate,
    TicketSnapshot,
    VerificationVerdict,
)
from core.routing.profile_registry import (
    RoutingProfileRegistry,
    get_default_profile_registry,
)
from core.routing.providers.catalog import CatalogCandidateProvider
from core.routing.providers.lexical import LexicalCandidateProvider
from core.routing.verifier.contracts import (
    PrivacyZone,
    VerifierRunResult,
    VerifierRunStatus,
)
from core.routing.verifier.service import GreyZoneVerifier
from core.routing.verifier.transport import VerifierTransport


class MockCandidateGenerator:
    def __init__(
        self,
        candidates: Sequence[ScenarioCandidate],
        evidence: Sequence[RoutingEvidence],
        degraded: dict[str, str] | None = None,
        registry: RoutingProfileRegistry | None = None,
    ) -> None:
        self._candidates = list(candidates)
        self._evidence = list(evidence)
        self._degraded = degraded or {}
        self.registry = registry or get_default_profile_registry()

    async def generate(self, snapshot: TicketSnapshot) -> CandidateGenerationResult:
        return CandidateGenerationResult(
            candidates=self._candidates,
            evidence=self._evidence,
            degraded_providers=self._degraded,
        )


class FakeVerifierTransport(VerifierTransport):
    """Deterministic fake transport returning pre-configured responses."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses: list[str] = responses or []
        self.calls: list[dict] = []

    async def complete_json(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        payload: dict,
        timeout_seconds: float,
    ) -> str:
        self.calls.append({
            "model_alias": model_alias,
            "system_prompt": system_prompt,
            "payload": payload,
            "timeout_seconds": timeout_seconds,
        })
        if not self.responses:
            raise RuntimeError("FakeVerifierTransport: No more responses configured")
        return self.responses.pop(0)


class MockGreyZoneVerifier:
    def __init__(self, run_result: VerifierRunResult) -> None:
        self._run_result = run_result
        self.call_count = 0

    async def verify(self, *, snapshot, candidates, evidence, profiles):
        self.call_count += 1
        return self._run_result


def _make_snapshot(
    task_id: int = 142135,
    title: str = "Подключение к WLAN",
    description: str = "Прошу предоставить доступ к корпоративной сети Wi-Fi",
    entities: dict[str, str] | None = None,
    custom_fields: dict[str, str] | None = None,
    service_id: int | None = None,
    service_name: str | None = None,
) -> TicketSnapshot:
    return TicketSnapshot(
        task_id=task_id,
        status_id=1,
        service_id=service_id,
        service_name=service_name,
        title=title,
        description=description,
        entities=entities or {},
        custom_fields=custom_fields or {},
        snapshot_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_exact_unique_service_id_selected_without_verifier():
    """Exact unique service_id candidate is directly selected without invoking verifier."""
    ev = RoutingEvidence(
        id="ev-1",
        candidate_key="account_lock",
        source=EvidenceSource.service_id,
        strength=EvidenceStrength.exact,
        polarity=EvidencePolarity.supports,
        source_ref="service_id",
    )
    cand = ScenarioCandidate(
        scenario_key="account_lock",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_id},
    )

    mock_gen = MockCandidateGenerator([cand], [ev])
    mock_ver = MockGreyZoneVerifier(
        VerifierRunResult(
            status=VerifierRunStatus.success,
            verifications=[CandidateVerification(scenario_key="account_lock", verdict=VerificationVerdict.supported)],
            prompt_version="candidate-verifier-v1",
            sanitizer_version="dlp-verifier-v1",
            requested_model_alias="helpdesk-local",
            privacy_zone=PrivacyZone.red,
            sanitized_input_hash="h" * 64,
        )
    )

    cascade = RoutingCascade(candidate_generator=mock_gen, verifier=mock_ver)
    snapshot = _make_snapshot(entities={"target_user": "ivanov.i"})

    decision = await cascade.decide(snapshot)

    assert mock_ver.call_count == 0  # Verifier NOT called!
    assert decision.state == RoutingState.selected
    assert decision.selected_scenario == "account_lock"
    assert decision.selected_scenario_version == "1.0.0"
    assert decision.router_version == ROUTER_VERSION
    assert decision.verifier_trace is None
    assert "direct_exact_service_id" in decision.decision_reason_codes


@pytest.mark.asyncio
async def test_single_lexical_candidate_calls_verifier():
    """A single lexical-only candidate goes through verifier and is verified."""
    ev = RoutingEvidence(
        id="ev-1",
        candidate_key="install_printer",
        source=EvidenceSource.title,
        strength=EvidenceStrength.strong,
        polarity=EvidencePolarity.supports,
        source_ref="title",
    )
    cand = ScenarioCandidate(
        scenario_key="install_printer",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.title},
    )

    mock_gen = MockCandidateGenerator([cand], [ev])
    mock_ver = MockGreyZoneVerifier(
        VerifierRunResult(
            status=VerifierRunStatus.success,
            verifications=[CandidateVerification(scenario_key="install_printer", verdict=VerificationVerdict.supported)],
            prompt_version="candidate-verifier-v1",
            sanitizer_version="dlp-verifier-v1",
            requested_model_alias="helpdesk-fast",
            privacy_zone=PrivacyZone.green,
            sanitized_input_hash="h" * 64,
        )
    )

    cascade = RoutingCascade(candidate_generator=mock_gen, verifier=mock_ver)
    snapshot = _make_snapshot(entities={"pc_name": "PC-01", "printer_address": "10.244.1.5"})

    decision = await cascade.decide(snapshot)

    assert mock_ver.call_count == 1
    assert decision.state == RoutingState.selected
    assert decision.selected_scenario == "install_printer"
    assert decision.verifier_trace is not None
    assert decision.verifier_trace.status == "success"
    assert "single_supported_candidate" in decision.decision_reason_codes


@pytest.mark.asyncio
async def test_regression_142135_empty_entities_needs_clarification():
    """Regression #142135: grant_wlan by service_name with empty entities.

    Must route to RED/local verifier, then return needs_clarification with target_user.
    """
    ev = RoutingEvidence(
        id="ev-1",
        candidate_key="grant_wlan",
        source=EvidenceSource.service_name,
        strength=EvidenceStrength.strong,
        polarity=EvidencePolarity.supports,
        source_ref="service_name",
    )
    cand = ScenarioCandidate(
        scenario_key="grant_wlan",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_name},
    )

    mock_gen = MockCandidateGenerator([cand], [ev])
    mock_ver = MockGreyZoneVerifier(
        VerifierRunResult(
            status=VerifierRunStatus.success,
            verifications=[CandidateVerification(scenario_key="grant_wlan", verdict=VerificationVerdict.supported)],
            prompt_version="candidate-verifier-v1",
            sanitizer_version="dlp-verifier-v1",
            requested_model_alias="helpdesk-local",
            privacy_zone=PrivacyZone.red,
            sanitized_input_hash="h" * 64,
        )
    )

    cascade = RoutingCascade(candidate_generator=mock_gen, verifier=mock_ver)
    # Empty entities
    snapshot = _make_snapshot(task_id=142135, entities={})

    decision = await cascade.decide(snapshot)

    assert mock_ver.call_count == 1
    assert decision.state == RoutingState.needs_clarification
    assert decision.selected_scenario == "grant_wlan"
    assert decision.missing_facts == ["target_user"]
    assert decision.verifier_trace is not None
    assert decision.verifier_trace.privacy_zone == "red"


@pytest.mark.asyncio
async def test_regression_142135_populated_user_selected():
    """Regression #142135: grant_wlan by service_name with populated target_user is selected."""
    ev = RoutingEvidence(
        id="ev-1",
        candidate_key="grant_wlan",
        source=EvidenceSource.service_name,
        strength=EvidenceStrength.strong,
        polarity=EvidencePolarity.supports,
        source_ref="service_name",
    )
    cand = ScenarioCandidate(
        scenario_key="grant_wlan",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_name},
    )

    mock_gen = MockCandidateGenerator([cand], [ev])
    mock_ver = MockGreyZoneVerifier(
        VerifierRunResult(
            status=VerifierRunStatus.success,
            verifications=[CandidateVerification(scenario_key="grant_wlan", verdict=VerificationVerdict.supported)],
            prompt_version="candidate-verifier-v1",
            sanitizer_version="dlp-verifier-v1",
            requested_model_alias="helpdesk-local",
            privacy_zone=PrivacyZone.red,
            sanitized_input_hash="h" * 64,
        )
    )

    cascade = RoutingCascade(candidate_generator=mock_gen, verifier=mock_ver)
    snapshot = _make_snapshot(task_id=142135, entities={"target_user": "petrov.p"})

    decision = await cascade.decide(snapshot)

    assert mock_ver.call_count == 1
    assert decision.state == RoutingState.selected
    assert decision.selected_scenario == "grant_wlan"
    assert decision.missing_facts == []


@pytest.mark.asyncio
async def test_verifier_degradation_handled_cleanly():
    """When verifier degrades, decision state is degraded with verifier machine code."""
    ev = RoutingEvidence(
        id="ev-1",
        candidate_key="grant_wlan",
        source=EvidenceSource.service_name,
        strength=EvidenceStrength.strong,
        polarity=EvidencePolarity.supports,
        source_ref="service_name",
    )
    cand = ScenarioCandidate(
        scenario_key="grant_wlan",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_name},
    )

    mock_gen = MockCandidateGenerator([cand], [ev])
    mock_ver = MockGreyZoneVerifier(
        VerifierRunResult(
            status=VerifierRunStatus.degraded,
            verifications=[],
            prompt_version="candidate-verifier-v1",
            sanitizer_version="dlp-verifier-v1",
            requested_model_alias="helpdesk-local",
            privacy_zone=PrivacyZone.red,
            sanitized_input_hash="h" * 64,
            degradation_reason="verifier_timeout",
        )
    )

    cascade = RoutingCascade(candidate_generator=mock_gen, verifier=mock_ver)
    snapshot = _make_snapshot()

    decision = await cascade.decide(snapshot)

    assert decision.state == RoutingState.degraded
    assert decision.degradation_reason == "verifier_timeout"
    assert decision.degraded_components.get("verifier") == "verifier_timeout"
    assert decision.selected_scenario is None


@pytest.mark.asyncio
async def test_zero_leakage_in_decision_dump():
    """Ensure sensitive passwords, Field1489, prompts, and exception traces are absent from decision dump."""
    ev = RoutingEvidence(
        id="ev-1",
        candidate_key="account_lock",
        source=EvidenceSource.service_id,
        strength=EvidenceStrength.exact,
        polarity=EvidencePolarity.supports,
        source_ref="service_id",
    )
    cand = ScenarioCandidate(
        scenario_key="account_lock",
        scenario_version="1.0.0",
        evidence_ids=["ev-1"],
        sources={EvidenceSource.service_id},
    )

    mock_gen = MockCandidateGenerator([cand], [ev])
    cascade = RoutingCascade(candidate_generator=mock_gen, verifier=None)
    snapshot = _make_snapshot(
        entities={"target_user": "ivanov.i"},
        custom_fields={"1489": "secret_pass_123", "password": "super_secret_pw"},
    )

    decision = await cascade.decide(snapshot)
    decision_json = decision.model_dump_json()

    assert "secret_pass_123" not in decision_json
    assert "super_secret_pw" not in decision_json
    assert "Traceback" not in decision_json
    assert "Exception" not in decision_json


def test_routing_cascade_requires_explicit_candidate_generator():
    """RoutingCascade cannot be instantiated without an explicit CandidateGenerator."""
    with pytest.raises(TypeError, match="explicit candidate_generator instance"):
        RoutingCascade(candidate_generator=None)  # type: ignore


@pytest.mark.asyncio
async def test_e2e_real_providers_direct_decision_without_mock_generator():
    """End-to-end domain smoke: Real providers generate candidates, direct exact winner selected."""
    registry = get_default_profile_registry()
    providers = [CatalogCandidateProvider(), LexicalCandidateProvider()]
    generator = CandidateGenerator(registry=registry, providers=providers)
    cascade = RoutingCascade(candidate_generator=generator, verifier=None)

    # service_id=8 matches account_lock
    snapshot = _make_snapshot(
        task_id=777001,
        service_id=8,
        title="Блокировка пользователя",
        description="Срочно заблокировать уволенного сотрудника",
        entities={"target_user": "petrov.p"},
    )

    decision = await cascade.decide(snapshot)

    assert decision.state == RoutingState.selected
    assert decision.selected_scenario == "account_lock"
    assert decision.selected_scenario_version is not None
    assert decision.missing_facts == []
    assert decision.verifier_trace is None
    assert "direct_exact_service_id" in decision.decision_reason_codes
    assert "facts_complete" in decision.decision_reason_codes
    assert any(c.scenario_key == "account_lock" for c in decision.candidates)
    assert any(e.candidate_key == "account_lock" and e.source == EvidenceSource.service_id for e in decision.evidence)


@pytest.mark.asyncio
async def test_e2e_real_providers_grey_zone_smoke_with_fake_transport():
    """End-to-end domain smoke: Real providers generate grey-zone candidates, Fake transport verifies."""
    registry = get_default_profile_registry()
    providers = [CatalogCandidateProvider(), LexicalCandidateProvider()]
    generator = CandidateGenerator(registry=registry, providers=providers)

    # Fake LLM response verifying grant_wlan
    fake_response = json.dumps({
        "verifications": [
            {
                "scenario_key": "grant_wlan",
                "verdict": "supported",
                "evidence_spans": ["Доступ к беспроводной сети"],
                "contradictions": [],
                "missing_information": [],
                "referenced_evidence_ids": [],
            }
        ]
    })
    transport = FakeVerifierTransport([fake_response])
    verifier = GreyZoneVerifier(transport=transport)

    cascade = RoutingCascade(candidate_generator=generator, verifier=verifier)

    # service_id=None, service_name matches grant_wlan (only 1 non-semantic source -> grey zone)
    snapshot = _make_snapshot(
        task_id=142135,
        service_id=None,
        service_name="Доступ к беспроводной сети",
        title="Заявка #142135",
        description="Прошу выдать права для рабочего ноутбука",
        entities={"target_user": "ivanov.i"},
    )

    decision = await cascade.decide(snapshot)

    assert len(transport.calls) == 1
    assert decision.state == RoutingState.selected
    assert decision.selected_scenario == "grant_wlan"
    assert decision.verifier_trace is not None
    assert decision.verifier_trace.status == "success"
    assert "single_supported_candidate" in decision.decision_reason_codes
    assert "facts_complete" in decision.decision_reason_codes

