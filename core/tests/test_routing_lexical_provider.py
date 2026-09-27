"""Unit tests for LexicalCandidateProvider."""

import pytest

from core.routing.contracts import (
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    SnapshotComment,
    TicketSnapshot,
)
from core.routing.profile_registry import get_default_profile_registry
from core.routing.providers.lexical import LexicalCandidateProvider


def _make_snapshot(
    title: str = "",
    description: str = "",
    public_comments: list[SnapshotComment] | None = None,
) -> TicketSnapshot:
    return TicketSnapshot(
        task_id=202,
        status_id=1,
        title=title,
        description=description,
        public_comments=public_comments or [],
        snapshot_hash="lexhash" * 8,
    )


@pytest.mark.asyncio
async def test_lexical_title_match():
    """Matching phrase in title creates evidence with source=title."""
    provider = LexicalCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(title="Требуется установка принтера в бухгалтерию")

    evidence = await provider.collect(snapshot, registry.list_all())
    printer_ev = next(e for e in evidence if e.candidate_key == "install_printer")
    assert printer_ev.source == EvidenceSource.title
    assert printer_ev.source_ref == "title"
    assert printer_ev.polarity == EvidencePolarity.supports
    assert printer_ev.strength == EvidenceStrength.strong
    assert printer_ev.text_span == "установка принтера"


@pytest.mark.asyncio
async def test_lexical_description_match():
    """Matching phrase in description creates evidence with source=description."""
    provider = LexicalCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(
        title="Заявка",
        description="Пожалуйста, сделайте принтер по умолчанию на рабочем месте WKS-1",
    )

    evidence = await provider.collect(snapshot, registry.list_all())
    default_ev = next(e for e in evidence if e.candidate_key == "default_printer_fix")
    assert default_ev.source == EvidenceSource.description
    assert default_ev.source_ref == "description"
    assert default_ev.text_span == "по умолчанию"


@pytest.mark.asyncio
async def test_lexical_comment_match():
    """Matching phrase in public comment creates evidence with source=comment and comment ID in source_ref."""
    provider = LexicalCandidateProvider()
    registry = get_default_profile_registry()
    comment = SnapshotComment(
        id=77,
        text="Также зависла печать документов на этом компьютере",
        author_name="user",
        is_private=False,
    )
    snapshot = _make_snapshot(title="Вопрос", description="Описание", public_comments=[comment])

    evidence = await provider.collect(snapshot, registry.list_all())
    spooler_ev = next(e for e in evidence if e.candidate_key == "printer_spooler_restart")
    assert spooler_ev.source == EvidenceSource.comment
    assert spooler_ev.source_ref == "comment:77"
    assert spooler_ev.text_span == "зависла печать"


@pytest.mark.asyncio
async def test_lexical_word_boundary_exclusion():
    """Single-word marker must not match inside another word."""
    provider = LexicalCandidateProvider()
    registry = get_default_profile_registry()
    # "беспринтерный" contains "принтер", but boundary should reject it
    snapshot = _make_snapshot(title="Обсуждение беспринтерный документооборот")

    evidence = await provider.collect(snapshot, registry.list_all())
    assert not any(e.text_span == "принтер" for e in evidence)


@pytest.mark.asyncio
async def test_lexical_exact_text_span_preserved():
    """Matched text_span preserves original casing and formatting."""
    provider = LexicalCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(title="Прошу настроить Wi-Fi на моем ноутбуке")

    evidence = await provider.collect(snapshot, registry.list_all())
    wlan_ev = next(e for e in evidence if e.candidate_key == "grant_wlan" and e.text_span == "Wi-Fi")
    assert wlan_ev.text_span == "Wi-Fi"


@pytest.mark.asyncio
async def test_stable_evidence_ids():
    """Repeated calls with identical snapshot yield identical evidence IDs."""
    provider = LexicalCandidateProvider()
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(title="Установка принтера", description="WKS-01")

    ev1 = await provider.collect(snapshot, registry.list_all())
    ev2 = await provider.collect(snapshot, registry.list_all())

    assert len(ev1) > 0
    assert [e.id for e in ev1] == [e.id for e in ev2]


@pytest.mark.asyncio
async def test_different_comments_yield_different_source_ref_and_id():
    """Same phrase in different comments generates distinct source_refs and distinct IDs."""
    provider = LexicalCandidateProvider()
    registry = get_default_profile_registry()
    c1 = SnapshotComment(id=10, text="Нужен Wi-Fi", author_name="u1")
    c2 = SnapshotComment(id=20, text="Повторно: нужен Wi-Fi", author_name="u2")
    snapshot = _make_snapshot(public_comments=[c1, c2])

    evidence = await provider.collect(snapshot, registry.list_all())
    wlan_evidence = [e for e in evidence if e.candidate_key == "grant_wlan"]

    assert len(wlan_evidence) == 2
    refs = {e.source_ref for e in wlan_evidence}
    ids = {e.id for e in wlan_evidence}

    assert refs == {"comment:10", "comment:20"}
    assert len(ids) == 2
