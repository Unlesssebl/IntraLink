"""Unit tests for SemanticCandidateProvider."""

import math
from typing import List, Optional

import pytest

from core.routing.contracts import EvidencePolarity, EvidenceSource, EvidenceStrength, TicketSnapshot
from core.routing.exceptions import ProviderUnavailableError
from core.routing.profile_registry import get_default_profile_registry
from core.routing.providers.semantic import SemanticCandidateProvider


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _make_fake_embedder(failure_mode: str = "none"):
    """Create deterministic fake vector generator for testing without network requests."""
    async def embed(text: str) -> Optional[List[float]]:
        if failure_mode == "total_failure":
            return None
        if failure_mode == "exception":
            raise RuntimeError("Fake network timeout to gateway")
        if failure_mode == "partial_prototype_fail" and "ошибка драйвера" in text:
            return None

        registry = get_default_profile_registry()
        scenario_keys = [p.scenario_key for p in registry.list_all()]
        dim = len(scenario_keys) + 1  # last dimension is for unrelated queries

        # If text is an exact prototype, return its scenario's basis vector
        for idx, key in enumerate(scenario_keys):
            prof = registry.get(key)
            if prof and text in prof.semantic_prototypes:
                vec = [0.0] * dim
                vec[idx] = 1.0
                return vec

        # If text is query, match intent keywords
        t = text.lower()
        if any(w in t for w in ("wi-fi", "wifi", "wlan", "вайфай")):
            idx = scenario_keys.index("grant_wlan")
            vec = [0.0] * dim
            vec[idx] = 1.0
            return vec
        if any(w in t for w in ("принтер", "мфу", "печать")):
            idx = scenario_keys.index("install_printer")
            vec = [0.0] * dim
            vec[idx] = 1.0
            return vec

        # Truly unrelated queries
        vec = [0.0] * dim
        vec[-1] = 1.0
        return vec

    return embed


def _make_snapshot(title: str = "", description: str = "") -> TicketSnapshot:
    return TicketSnapshot(
        task_id=303,
        status_id=1,
        title=title,
        description=description,
        snapshot_hash="semhash" * 8,
    )


@pytest.mark.asyncio
async def test_semantic_retrieval_and_evidence():
    """Valid text retrieves matching scenario and generates weak supporting evidence."""
    fake_fn = _make_fake_embedder()
    provider = SemanticCandidateProvider(embedder_fn=fake_fn, top_k=2, retrieval_floor=0.45)
    registry = get_default_profile_registry()

    snapshot = _make_snapshot(
        title="Прошу предоставить доступ к корпоративной сети",
        description="Не могу подключиться к wi-fi",
    )

    evidence = await provider.collect(snapshot, registry.list_all())
    assert len(evidence) >= 1
    wlan_ev = next(e for e in evidence if e.candidate_key == "grant_wlan")
    assert wlan_ev.source == EvidenceSource.semantic
    assert wlan_ev.polarity == EvidencePolarity.supports
    assert wlan_ev.strength == EvidenceStrength.weak
    assert "semantic_provider_v1:" in wlan_ev.source_ref
    assert wlan_ev.text_span is None


@pytest.mark.asyncio
async def test_semantic_top_k_limiting():
    """Provider limits emitted candidate scenarios to configured top_k."""
    fake_fn = _make_fake_embedder()
    provider = SemanticCandidateProvider(embedder_fn=fake_fn, top_k=2, retrieval_floor=0.0)
    registry = get_default_profile_registry()

    snapshot = _make_snapshot(title="Печать принтер документ", description="wi-fi 1с")
    evidence = await provider.collect(snapshot, registry.list_all())
    assert len(evidence) <= 2


@pytest.mark.asyncio
async def test_semantic_retrieval_floor():
    """Phrases below retrieval floor produce zero evidence."""
    fake_fn = _make_fake_embedder()
    # Floor 0.90 is higher than neutral vector similarity (0.50)
    provider = SemanticCandidateProvider(embedder_fn=fake_fn, retrieval_floor=0.90)
    registry = get_default_profile_registry()

    snapshot = _make_snapshot(title="Какая-то совершенно случайная фраза", description="просто текст")
    evidence = await provider.collect(snapshot, registry.list_all())
    assert len(evidence) == 0


@pytest.mark.asyncio
async def test_semantic_empty_text_no_embedding_call():
    """Empty snapshot text returns empty evidence without calling embedder."""
    called = False

    async def fail_if_called(text: str) -> Optional[List[float]]:
        nonlocal called
        called = True
        return [1.0, 0.0]

    provider = SemanticCandidateProvider(embedder_fn=fail_if_called)
    # Warm up with fake vectors directly
    provider._is_warmed_up = True
    provider._prototype_cache = {"grant_wlan": [[1.0, 0.0]]}

    snapshot = _make_snapshot(title="", description="")
    evidence = await provider.collect(snapshot, [])
    assert evidence == []
    assert not called


@pytest.mark.asyncio
async def test_partial_prototype_failure_does_not_abort():
    """Failure embedding a single prototype does not crash warm_up or retrieval."""
    fake_fn = _make_fake_embedder(failure_mode="partial_prototype_fail")
    provider = SemanticCandidateProvider(embedder_fn=fake_fn)
    registry = get_default_profile_registry()

    await provider.warm_up(registry.list_all())
    assert provider._is_warmed_up is True
    # install_printer has multiple prototypes; one failed, remainder cached
    assert len(provider._prototype_cache["install_printer"]) > 0


@pytest.mark.asyncio
async def test_total_embedder_unavailability_raises_provider_error():
    """Total failure to embed raises controlled ProviderUnavailableError."""
    fake_fn = _make_fake_embedder(failure_mode="total_failure")
    provider = SemanticCandidateProvider(embedder_fn=fake_fn)
    registry = get_default_profile_registry()

    snapshot = _make_snapshot(title="Подключение wi-fi")
    with pytest.raises(ProviderUnavailableError, match="unavailable"):
        await provider.collect(snapshot, registry.list_all())


@pytest.mark.asyncio
async def test_stable_semantic_evidence_id():
    """Identical signals generate identical evidence ID across repeated runs."""
    fake_fn = _make_fake_embedder()
    provider = SemanticCandidateProvider(embedder_fn=fake_fn)
    registry = get_default_profile_registry()
    snapshot = _make_snapshot(title="Wi-Fi доступ", description="ноутбук")

    ev1 = await provider.collect(snapshot, registry.list_all())
    ev2 = await provider.collect(snapshot, registry.list_all())

    assert len(ev1) > 0
    assert [e.id for e in ev1] == [e.id for e in ev2]
