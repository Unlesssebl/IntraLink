"""Tests for RAG-Augmented Semantic Routing (SemanticPrototypeIndex + Factor E in ScenarioRouter).

Test strategy:
- Unit tests for SemanticPrototypeIndex with mocked embeddings (no LiteLLM dependency).
- Integration tests for ScenarioRouter verifying that Factor E correctly boosts
  the winning candidate when semantic similarity is injected.
- Regression tests ensuring graceful degradation when embedder returns None.
"""

from __future__ import annotations

import math
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from core.intraservice.dto import TaskDTO
from worker.src.scenarios.router import ScenarioRouter
from worker.src.scenarios.semantic_index import (
    SCENARIO_PROTOTYPES,
    SemanticPrototypeIndex,
    _cosine_similarity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_unit_vec(dim: int, axis: int) -> List[float]:
    """Return a unit vector along `axis` in `dim`-dimensional space."""
    v = [0.0] * dim
    v[axis] = 1.0
    return v


def _make_similar_vec(base: List[float], noise: float = 0.05) -> List[float]:
    """Return a vector close to `base` with a small additive perturbation."""
    perturbed = [x + noise for x in base]
    norm = math.sqrt(sum(x * x for x in perturbed))
    return [x / norm for x in perturbed]


# ---------------------------------------------------------------------------
# 1. _cosine_similarity unit tests
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-9

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(_cosine_similarity(a, b) - (-1.0)) < 1e-9

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0]
        b = [1.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_mismatched_lengths_returns_zero(self):
        assert _cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


# ---------------------------------------------------------------------------
# 2. SemanticPrototypeIndex – warm-up and scoring with mocked embedder
# ---------------------------------------------------------------------------

class TestSemanticPrototypeIndex:
    """Unit tests for SemanticPrototypeIndex using synthetic vectors."""

    @pytest.fixture
    def index_with_mock_client(self) -> SemanticPrototypeIndex:
        """Return a SemanticPrototypeIndex with a mocked AsyncOpenAI client."""
        mock_client = MagicMock()
        return SemanticPrototypeIndex(ai_client=mock_client)

    @pytest.mark.asyncio
    async def test_warm_up_populates_prototype_vectors(self, index_with_mock_client):
        """Warm-up must produce one prototype vector per phrase per scenario."""
        index = index_with_mock_client
        dim = 16  # small for test speed

        call_count = 0

        async def fake_embed(text, ai_client, model_name="bge-m3"):
            nonlocal call_count
            call_count += 1
            # Return a deterministic unit vector based on hash of text
            axis = hash(text) % dim
            return _make_unit_vec(dim, axis)

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=fake_embed):
            await index.warm_up()

        assert index.is_ready is True
        total_expected = sum(len(phrases) for phrases in SCENARIO_PROTOTYPES.values())
        assert call_count == total_expected
        for scenario_key, phrases in SCENARIO_PROTOTYPES.items():
            assert len(index._prototype_vectors[scenario_key]) == len(phrases)

    @pytest.mark.asyncio
    async def test_warm_up_is_idempotent(self, index_with_mock_client):
        """Calling warm_up twice should not re-embed prototypes."""
        index = index_with_mock_client
        call_count = 0

        async def fake_embed(text, ai_client, model_name="bge-m3"):
            nonlocal call_count
            call_count += 1
            return [1.0 / math.sqrt(4)] * 4  # normalized 4-dim vector

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=fake_embed):
            await index.warm_up()
            first_count = call_count
            await index.warm_up()  # Second call – must be no-op
            assert call_count == first_count, "warm_up() called embedder twice!"

    @pytest.mark.asyncio
    async def test_graceful_degradation_when_embedder_fails(self, index_with_mock_client):
        """When all embedding calls fail, index must not be ready and score() returns {}."""
        index = index_with_mock_client

        async def always_fails(text, ai_client, model_name="bge-m3"):
            return None

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=always_fails):
            await index.warm_up()

        assert index.is_ready is False
        scores = await index.score("не включается компьютер")
        assert scores == {}

    @pytest.mark.asyncio
    async def test_score_returns_highest_for_correct_scenario(self, index_with_mock_client):
        """Scenario whose prototype cluster is closest to query must score highest."""
        index = index_with_mock_client
        dim = 8
        # Assign unique axis to each scenario
        scenario_keys = list(SCENARIO_PROTOTYPES.keys())
        scenario_axis = {key: i for i, key in enumerate(scenario_keys)}

        async def fake_embed(text, ai_client, model_name="bge-m3"):
            # Prototypes of a scenario get their scenario's axis
            for key, phrases in SCENARIO_PROTOTYPES.items():
                if text in phrases:
                    return _make_unit_vec(dim, scenario_axis[key])
            # Query – we'll patch it separately in score()
            return None

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=fake_embed):
            await index.warm_up()

        assert index.is_ready is True

        # Now test score() with a query vector aligned to "install_printer" axis
        target_key = "install_printer"
        query_vec = _make_unit_vec(dim, scenario_axis[target_key])

        async def fake_query_embed(text, ai_client, model_name="bge-m3"):
            return query_vec

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=fake_query_embed):
            scores = await index.score("Установите принтер на рабочей станции")

        assert target_key in scores
        assert scores[target_key] == pytest.approx(1.0, abs=1e-6)
        # All other scenarios should be orthogonal → score ≈ 0
        for k, v in scores.items():
            if k != target_key:
                assert v == pytest.approx(0.0, abs=1e-6), f"Unexpected score for '{k}': {v}"

    @pytest.mark.asyncio
    async def test_score_returns_empty_when_not_ready(self, index_with_mock_client):
        """score() must return {} if warm_up was never called."""
        index = index_with_mock_client
        # No warm_up called → not ready
        scores = await index.score("текст заявки")
        assert scores == {}

    @pytest.mark.asyncio
    async def test_score_returns_empty_for_empty_text(self, index_with_mock_client):
        """score() must return {} for empty or whitespace-only text."""
        index = index_with_mock_client

        async def fake_embed(text, ai_client, model_name="bge-m3"):
            return [1.0, 0.0]

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=fake_embed):
            await index.warm_up()

        scores = await index.score("   ")
        assert scores == {}

    @pytest.mark.asyncio
    async def test_score_handles_query_embed_failure(self, index_with_mock_client):
        """If query embedding fails, score() must return {} without raising."""
        index = index_with_mock_client
        call_count = [0]

        async def embed_prototypes_then_fail(text, ai_client, model_name="bge-m3"):
            call_count[0] += 1
            # First N calls (warm-up) succeed; subsequent (query) fail
            total_prototypes = sum(len(v) for v in SCENARIO_PROTOTYPES.values())
            if call_count[0] <= total_prototypes:
                return [1.0, 0.0, 0.0, 0.0]
            return None

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=embed_prototypes_then_fail):
            await index.warm_up()
            scores = await index.score("запрос который провалится")

        assert scores == {}


# ---------------------------------------------------------------------------
# 3. ScenarioRouter – Factor E integration tests
# ---------------------------------------------------------------------------

class TestRouterFactorE:
    """Verify that semantic scores (Factor E) influence routing decisions."""

    @pytest.fixture
    def router_no_semantic(self) -> ScenarioRouter:
        """Router with ai_client=None → SemanticPrototypeIndex created but won't be ready."""
        return ScenarioRouter(ai_client=None)

    @pytest.fixture
    def router_with_warm_semantic(self) -> ScenarioRouter:
        """Router with a pre-warmed semantic index using synthetic unit vectors."""
        router = ScenarioRouter(ai_client=None)
        # Manually inject a pre-warmed index with synthetic prototypes
        dim = 8
        scenario_keys = list(SCENARIO_PROTOTYPES.keys())
        scenario_axis = {key: i for i, key in enumerate(scenario_keys)}

        # Inject synthetic vectors directly bypassing actual warm_up
        index = router._semantic_index
        for key in SCENARIO_PROTOTYPES:
            index._prototype_vectors[key] = [_make_unit_vec(dim, scenario_axis[key])]
        index._is_ready = True
        return router, scenario_axis, dim

    @pytest.mark.asyncio
    async def test_factor_e_contributes_to_score_reasoning(self, router_with_warm_semantic):
        """Factor E reasons must appear in the winning match when semantic boosts apply."""
        from worker.src.scenarios.registry import get_default_scenario_registry, reset_registry
        reset_registry()
        registry = get_default_scenario_registry()

        router, scenario_axis, dim = router_with_warm_semantic
        target_key = "install_printer"
        query_vec = _make_unit_vec(dim, scenario_axis[target_key])

        async def fake_query_embed(text, ai_client, model_name="bge-m3"):
            return query_vec

        task = TaskDTO(
            Id=300,
            Name="Установить принтер в кабинет",
            Description="Подключите Canon LBP к рабочей станции WKS-301",
            ServiceId=62,  # Strong catalog prior
        )

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=fake_query_embed):
            result = await router.route_task(task, registry._scenarios)

        assert result is not None
        scenario, match = result
        assert scenario.scenario_key == target_key
        # Factor E must appear in reasoning
        factor_e_reasons = [r for r in match.reasons if "Factor E" in r]
        assert len(factor_e_reasons) > 0, "Factor E did not contribute to match reasoning"
        assert match.confidence >= 0.55

    @pytest.mark.asyncio
    async def test_factor_e_disabled_falls_back_gracefully(self, router_no_semantic):
        """When semantic index not ready, router must still route via A+B+C+D factors."""
        from worker.src.scenarios.ad_password_reset import ADPasswordResetScenario
        from worker.src.scenarios.registry import get_default_scenario_registry, reset_registry
        reset_registry()
        registry = get_default_scenario_registry()
        registry.register(ADPasswordResetScenario())

        router = router_no_semantic
        # Semantic index created but not warmed up → is_ready = False

        task = TaskDTO(
            Id=301,
            Name="Сброс пароля в домене",
            Description="Забыл пароль от учетной записи Active Directory",
            ServiceId=23,  # Password reset catalog
            ApplicantName="Козлов К.К.",
        )

        result = await router.route_task(task, registry._scenarios)
        assert result is not None
        scenario, match = result
        assert scenario.scenario_key == "ad_password_reset"
        assert match.matched is True
        # No Factor E in reasons since index not ready
        factor_e_reasons = [r for r in match.reasons if "Factor E" in r]
        assert len(factor_e_reasons) == 0

    @pytest.mark.asyncio
    async def test_semantic_tiebreaker_resolves_ambiguity(self, router_with_warm_semantic):
        """Verify SemanticPrototypeIndex correctly identifies the closest scenario by cosine similarity.

        Tests index.score() directly to decouple from rag_consultation's broad can_handle().
        """
        router, scenario_axis, dim = router_with_warm_semantic
        target_key = "offline_host"
        query_vec = _make_unit_vec(dim, scenario_axis[target_key])

        async def fake_query_embed(text, ai_client, model_name="bge-m3"):
            return query_vec

        index = router._semantic_index
        assert index.is_ready is True, "Semantic index must be ready after fixture warm-up"

        with patch("worker.src.scenarios.semantic_index.get_embedding_vector", side_effect=fake_query_embed):
            scores = await index.score("Проблема с рабочим местом Оборудование не работает")

        assert target_key in scores, f"'{target_key}' missing from semantic scores"
        assert scores[target_key] == pytest.approx(1.0, abs=1e-6), (
            f"Expected score 1.0 for '{target_key}', got {scores[target_key]}"
        )
        for k, v in scores.items():
            if k != target_key:
                assert v == pytest.approx(0.0, abs=1e-6), (
                    f"Expected score 0.0 for '{k}' (orthogonal), got {v}"
                )


# ---------------------------------------------------------------------------
# 4. Prototype catalog completeness
# ---------------------------------------------------------------------------

class TestPrototypeCatalog:
    def test_all_core6_scenarios_have_prototypes(self):
        """Every Core-6 scenario must have at least 3 prototype phrases."""
        required = {"install_printer", "ad_password_reset", "grant_wlan", "offline_host",
                    "service_redirect", "rag_consultation"}
        for key in required:
            assert key in SCENARIO_PROTOTYPES, f"No prototypes for '{key}'"
            assert len(SCENARIO_PROTOTYPES[key]) >= 3, (
                f"Scenario '{key}' has only {len(SCENARIO_PROTOTYPES[key])} prototypes (min 3 required)"
            )

    def test_prototype_phrases_non_empty(self):
        """All prototype phrases must be non-empty strings."""
        for key, phrases in SCENARIO_PROTOTYPES.items():
            for phrase in phrases:
                assert isinstance(phrase, str) and len(phrase.strip()) > 5, (
                    f"Empty or too-short prototype in '{key}': '{phrase}'"
                )
