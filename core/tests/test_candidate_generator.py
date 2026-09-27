"""Unit tests for CandidateGenerator and Regression #142135 verification."""

import pytest

from core.routing.candidate_generator import CandidateGenerator
from core.routing.contracts import (
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingEvidence,
    TicketSnapshot,
)
from core.routing.evidence_id import compute_evidence_id
from core.routing.exceptions import ProviderUnavailableError
from core.routing.profile_registry import get_default_profile_registry
from core.routing.providers.catalog import CatalogCandidateProvider
from core.routing.providers.lexical import LexicalCandidateProvider


class MockFailingProvider:
    """Mock provider simulating an external failure (e.g. gateway timeout or crash)."""

    name: str = "failing_mock"

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def collect(self, snapshot, profiles):
        raise self.exc


class MockCustomEvidenceProvider:
    """Mock provider returning explicit pre-configured evidence."""

    name: str = "custom_mock"

    def __init__(self, evidence: list[RoutingEvidence]) -> None:
        self._evidence = evidence

    async def collect(self, snapshot, profiles):
        return list(self._evidence)


def _make_snapshot(
    service_id: int | None = None,
    service_name: str | None = None,
    title: str = "",
    description: str = "",
    entities: dict[str, str] | None = None,
) -> TicketSnapshot:
    return TicketSnapshot(
        task_id=505,
        status_id=1,
        service_id=service_id,
        service_name=service_name,
        title=title,
        description=description,
        entities=entities or {},
        snapshot_hash="genhash" * 8,
    )


@pytest.mark.asyncio
async def test_merge_evidence_from_different_providers():
    """CandidateGenerator aggregates evidence from multiple providers into structured candidates."""
    registry = get_default_profile_registry()
    catalog_p = CatalogCandidateProvider()
    lexical_p = LexicalCandidateProvider()

    generator = CandidateGenerator(registry=registry, providers=[catalog_p, lexical_p])
    snapshot = _make_snapshot(
        service_id=63,
        service_name="Корпоративный Wi-Fi",
        title="Доступ к Wi-Fi",
        description="Прошу выдать доступ",
    )

    result = await generator.generate(snapshot)

    assert len(result.degraded_providers) == 0
    candidate_keys = {c.scenario_key for c in result.candidates}
    assert "grant_wlan" in candidate_keys

    wlan_cand = next(c for c in result.candidates if c.scenario_key == "grant_wlan")
    # Must have both service_id and title/service_name in sources
    assert EvidenceSource.service_id in wlan_cand.sources
    assert len(wlan_cand.evidence_ids) >= 2


@pytest.mark.asyncio
async def test_deterministic_sorting():
    """Candidates are sorted alphabetically by scenario_key, evidence by ID."""
    registry = get_default_profile_registry()
    catalog_p = CatalogCandidateProvider()
    generator = CandidateGenerator(registry=registry, providers=[catalog_p])

    # Service 19 triggers install_printer, printer_spooler_restart, default_printer_fix
    snapshot = _make_snapshot(service_id=19)
    result = await generator.generate(snapshot)

    keys = [c.scenario_key for c in result.candidates]
    assert keys == sorted(keys)

    ids = [e.id for e in result.evidence]
    assert ids == sorted(ids)

    for c in result.candidates:
        assert c.evidence_ids == sorted(c.evidence_ids)


@pytest.mark.asyncio
async def test_exact_evidence_deduplication():
    """Exact duplicate evidence across providers is deduplicated without error."""
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(title="Тест")

    ev_id = compute_evidence_id(
        provider_name="shared_source",
        snapshot_hash=snapshot.snapshot_hash,
        candidate_key="install_printer",
        source=EvidenceSource.title,
        polarity=EvidencePolarity.supports,
        strength=EvidenceStrength.strong,
        source_ref="title",
        text_span="принтер",
    )
    shared_ev = RoutingEvidence(
        id=ev_id,
        candidate_key="install_printer",
        source=EvidenceSource.title,
        polarity=EvidencePolarity.supports,
        strength=EvidenceStrength.strong,
        source_ref="title",
        text_span="принтер",
    )

    p1 = MockCustomEvidenceProvider([shared_ev])
    p2 = MockCustomEvidenceProvider([shared_ev])
    generator = CandidateGenerator(registry=registry, providers=[p1, p2])

    result = await generator.generate(snapshot)
    assert len(result.evidence) == 1
    assert len(result.degraded_providers) == 0


@pytest.mark.asyncio
async def test_semantic_failure_does_not_abort_catalog_and_lexical():
    """Degradation of semantic provider marks provider as degraded without aborting other providers."""
    registry = get_default_profile_registry()
    catalog_p = CatalogCandidateProvider()
    failing_semantic = MockFailingProvider(ProviderUnavailableError("Gateway unreachable"))

    generator = CandidateGenerator(registry=registry, providers=[catalog_p, failing_semantic])
    snapshot = _make_snapshot(service_id=63)

    result = await generator.generate(snapshot)

    assert result.degraded_providers == {"failing_mock": "provider_unavailable"}
    assert len(result.candidates) == 1
    assert result.candidates[0].scenario_key == "grant_wlan"


@pytest.mark.asyncio
async def test_unknown_scenario_key_discarded():
    """Evidence referencing unknown scenario_key is discarded."""
    registry = get_default_profile_registry()
    snapshot = _make_snapshot()

    rogue_ev = RoutingEvidence(
        id="rogue-id",
        candidate_key="rogue_unknown_scenario",
        source=EvidenceSource.title,
        polarity=EvidencePolarity.supports,
        strength=EvidenceStrength.strong,
        source_ref="title",
    )
    p = MockCustomEvidenceProvider([rogue_ev])
    generator = CandidateGenerator(registry=registry, providers=[p])

    result = await generator.generate(snapshot)
    assert len(result.candidates) == 0
    assert len(result.evidence) == 0


@pytest.mark.asyncio
async def test_conflicting_duplicate_id_marks_provider_invalid():
    """Conflicting evidence items sharing identical ID with different attributes marks provider invalid."""
    registry = get_default_profile_registry()
    snapshot = _make_snapshot()

    ev1 = RoutingEvidence(
        id="conflict-id",
        candidate_key="install_printer",
        source=EvidenceSource.title,
        polarity=EvidencePolarity.supports,
        strength=EvidenceStrength.strong,
        source_ref="title",
        text_span="принтер",
    )
    ev2 = RoutingEvidence(
        id="conflict-id",
        candidate_key="install_printer",
        source=EvidenceSource.description,  # Different source attribute!
        polarity=EvidencePolarity.supports,
        strength=EvidenceStrength.strong,
        source_ref="description",
        text_span="принтер",
    )

    p1 = MockCustomEvidenceProvider([ev1])
    p2 = MockCustomEvidenceProvider([ev2])
    generator = CandidateGenerator(registry=registry, providers=[p1, p2])

    result = await generator.generate(snapshot)
    assert "provider_invalid_result" in result.degraded_providers.values()


@pytest.mark.asyncio
async def test_no_winner_and_no_rag_fallback():
    """Absence of candidates does NOT select winner and does NOT invent rag_consultation fallback."""
    registry = get_default_profile_registry()
    catalog_p = CatalogCandidateProvider()
    generator = CandidateGenerator(registry=registry, providers=[catalog_p])

    snapshot = _make_snapshot(service_id=None, service_name=None, title="", description="")
    result = await generator.generate(snapshot)

    assert len(result.candidates) == 0
    assert len(result.evidence) == 0
    # No artificial fallback
    assert not any(c.scenario_key == "rag_consultation" for c in result.candidates)


@pytest.mark.asyncio
async def test_regression_142135_synthetic_wlan_preservation():
    """Regression test 142135: synthetic snapshot with service_name Wi-Fi must generate grant_wlan candidate."""
    registry = get_default_profile_registry()
    catalog_p = CatalogCandidateProvider()
    lexical_p = LexicalCandidateProvider()
    generator = CandidateGenerator(registry=registry, providers=[catalog_p, lexical_p])

    # Preconditions specified in prompt:
    # 1. service_name contains confirmed Wi-Fi/WLAN term
    # 2. ServiceId is missing or non-WLAN
    # 3. title/description do NOT contain direct Wi-Fi phrases
    # 4. entities are completely empty
    snapshot = _make_snapshot(
        service_id=None,
        service_name="Корпоративный Wi-Fi доступ",
        title="Проблема с подключением",
        description="Не могу получить доступ с рабочего места в кабинете 204",
        entities={},
    )

    result = await generator.generate(snapshot)

    # Assertions:
    # - grant_wlan is present among candidates
    candidate_keys = {c.scenario_key for c in result.candidates}
    assert "grant_wlan" in candidate_keys

    wlan_cand = next(c for c in result.candidates if c.scenario_key == "grant_wlan")
    # - evidence source is service_name
    assert EvidenceSource.service_name in wlan_cand.sources

    # - CandidateGenerator does NOT select a winner (it returns a set of candidates)
    assert isinstance(result.candidates, list)

    # - empty entities do NOT remove the candidate
    assert len(wlan_cand.evidence_ids) >= 1
