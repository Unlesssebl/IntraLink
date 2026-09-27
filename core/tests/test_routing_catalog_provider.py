"""Unit tests for CatalogCandidateProvider."""

import pytest

from core.routing.contracts import EvidencePolarity, EvidenceSource, EvidenceStrength, TicketSnapshot
from core.routing.profile_registry import get_default_profile_registry
from core.routing.providers.catalog import CatalogCandidateProvider


def _make_snapshot(
    service_id: int | None = None,
    service_name: str | None = None,
    title: str = "Test ticket",
    description: str = "Description",
) -> TicketSnapshot:
    return TicketSnapshot(
        task_id=101,
        status_id=1,
        service_id=service_id,
        service_name=service_name,
        title=title,
        description=description,
        snapshot_hash="h" * 64,
    )


@pytest.mark.asyncio
async def test_exact_service_id_evidence():
    """Exact service_id generates supports evidence with strength=exact."""
    provider = CatalogCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(service_id=63, service_name=None)

    evidence = await provider.collect(snapshot, registry.list_all())
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.candidate_key == "grant_wlan"
    assert ev.source == EvidenceSource.service_id
    assert ev.polarity == EvidencePolarity.supports
    assert ev.strength == EvidenceStrength.exact
    assert ev.source_ref == "service_id:63"
    assert ev.text_span is None


@pytest.mark.asyncio
async def test_service_name_term_evidence():
    """Normalized service_name match generates supports evidence with strength=strong and exact text span."""
    provider = CatalogCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(service_id=None, service_name="Подключение к корпоративному Wi-Fi")

    evidence = await provider.collect(snapshot, registry.list_all())
    assert len(evidence) >= 1
    wlan_ev = next(e for e in evidence if e.candidate_key == "grant_wlan")
    assert wlan_ev.source == EvidenceSource.service_name
    assert wlan_ev.polarity == EvidencePolarity.supports
    assert wlan_ev.strength == EvidenceStrength.strong
    assert "service_name:" in wlan_ev.source_ref
    assert wlan_ev.text_span == "Wi-Fi"


@pytest.mark.asyncio
async def test_multiple_candidates_for_common_printer_service():
    """Shared service 19 emits evidence for multiple printer candidates."""
    provider = CatalogCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(service_id=19, service_name="Обслуживание оргтехники и заправка картриджей")

    evidence = await provider.collect(snapshot, registry.list_all())
    candidates = {e.candidate_key for e in evidence}

    assert "install_printer" in candidates
    assert "printer_spooler_restart" in candidates
    assert "default_printer_fix" in candidates


@pytest.mark.asyncio
async def test_empty_service_fields_yield_empty_evidence():
    """Empty service_id and empty service_name produce zero evidence."""
    provider = CatalogCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(service_id=None, service_name=None)

    evidence = await provider.collect(snapshot, registry.list_all())
    assert len(evidence) == 0


@pytest.mark.asyncio
async def test_no_automatic_rag_fallback():
    """Absence of candidates does NOT produce rag_consultation fallback evidence."""
    provider = CatalogCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(service_id=99999, service_name="Неизвестный сторонний сервис")

    evidence = await provider.collect(snapshot, registry.list_all())
    assert not any(e.candidate_key == "rag_consultation" for e in evidence)
