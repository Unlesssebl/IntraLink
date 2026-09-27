"""SemanticCandidateProvider for Evidence-Based Routing Cascade.

Performs vector prototype retrieval using LiteLLM/OpenAI embeddings adapter.
Cosine similarity is strictly used as an internal retrieval filter to select
top-K candidate scenarios, never recorded as a decision probability or confidence.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from openai import AsyncOpenAI

from core.rag.embedder import get_embedding_vector
from core.routing.contracts import (
    EvidencePolarity,
    EvidenceSource,
    EvidenceStrength,
    RoutingEvidence,
    TicketSnapshot,
)
from core.routing.evidence_id import compute_evidence_id
from core.routing.exceptions import ProviderUnavailableError
from core.routing.profiles import ScenarioRoutingProfile

logger = logging.getLogger("core.routing.providers.semantic")

_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000/v1")
_LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-intraservice-master-key")


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two dense float vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCandidateProvider:
    """Vector prototype retrieval provider with top-K candidates and retrieval floor."""

    name: str = "semantic"

    def __init__(
        self,
        ai_client: Optional[AsyncOpenAI] = None,
        embedder_fn: Optional[Callable[[str], Awaitable[Optional[List[float]]]]] = None,
        text_sanitizer: Optional[Callable[[str], str]] = None,
        top_k: int = 3,
        retrieval_floor: float = 0.45,
    ) -> None:
        """Initialize provider with configurable parameters.

        Args:
            ai_client: Optional AsyncOpenAI client instance.
            embedder_fn: Optional mock/callable vector generator for unit tests.
            text_sanitizer: Optional pre-embed DLP sanitization hook.
            top_k: Maximum candidate scenarios to emit (default 3).
            retrieval_floor: Technical retrieval recall floor (default 0.45).
                This is strictly a retrieval recall filter to discard irrelevant
                background noise, NOT a final decision threshold.
        """
        self._ai_client = ai_client
        self._embedder_fn = embedder_fn
        self._text_sanitizer = text_sanitizer
        self.top_k = top_k
        self.retrieval_floor = retrieval_floor
        self._prototype_cache: Dict[str, List[List[float]]] = {}
        self._is_warmed_up: bool = False

    def _get_ai_client(self) -> AsyncOpenAI:
        if self._ai_client is None:
            self._ai_client = AsyncOpenAI(
                base_url=_LITELLM_BASE_URL,
                api_key=_LITELLM_API_KEY,
                timeout=5.0,
                max_retries=1,
            )
        return self._ai_client

    async def _embed(self, text: str) -> Optional[List[float]]:
        """Compute embedding vector via injected function or project embedder."""
        if self._embedder_fn is not None:
            return await self._embedder_fn(text)
        return await get_embedding_vector(text, self._get_ai_client())

    async def warm_up(self, profiles: Sequence[ScenarioRoutingProfile]) -> None:
        """Idempotently compute and cache dense prototype embeddings for scenario profiles.

        Errors on individual prototype phrases are isolated and do not abort initialization.
        """
        success_count = 0
        total_prototypes = 0

        for profile in profiles:
            if profile.scenario_key in self._prototype_cache:
                continue

            proto_vectors: List[List[float]] = []
            for phrase in profile.semantic_prototypes:
                total_prototypes += 1
                try:
                    vec = await self._embed(phrase)
                    if vec is not None:
                        proto_vectors.append(vec)
                        success_count += 1
                    else:
                        logger.warning(
                            "SemanticCandidateProvider: null vector for prototype of scenario '%s'",
                            profile.scenario_key,
                        )
                except Exception as exc:
                    logger.warning(
                        "SemanticCandidateProvider: error embedding prototype for scenario '%s': %s",
                        profile.scenario_key,
                        type(exc).__name__,
                    )

            self._prototype_cache[profile.scenario_key] = proto_vectors

        if total_prototypes > 0 and success_count == 0:
            logger.error(
                "SemanticCandidateProvider: failed to vectorise any prototypes (%d requested). Embedding service unavailable.",
                total_prototypes,
            )
            self._is_warmed_up = False
        else:
            self._is_warmed_up = True

    async def collect(
        self,
        snapshot: TicketSnapshot,
        profiles: Sequence[ScenarioRoutingProfile],
    ) -> List[RoutingEvidence]:
        """Collect top-K semantic candidate evidence items above the retrieval floor.

        Raises:
            ProviderUnavailableError: If the embedding gateway is unreachable.
        """
        if not self._is_warmed_up or not self._prototype_cache:
            await self.warm_up(profiles)

        # Check if prototype vectors are available
        has_cached_vectors = any(len(v) > 0 for v in self._prototype_cache.values())
        if not has_cached_vectors:
            raise ProviderUnavailableError("Embedding service unavailable: zero prototype vectors cached.")

        # Build query text strictly from title, description and public comments
        text_parts: List[str] = []
        if snapshot.title and snapshot.title.strip():
            text_parts.append(snapshot.title.strip())
        if snapshot.description and snapshot.description.strip():
            text_parts.append(snapshot.description.strip())
        for comment in snapshot.public_comments:
            if not comment.is_private and comment.text and comment.text.strip():
                text_parts.append(comment.text.strip())

        query_text = " \n".join(text_parts).strip()
        if not query_text:
            return []

        # DLP / Red-zone hook before embedding call
        if self._text_sanitizer is not None:
            query_text = self._text_sanitizer(query_text)

        logger.debug(
            "SemanticCandidateProvider: embedding query text (length=%d chars)",
            len(query_text),
        )

        try:
            query_vec = await self._embed(query_text)
        except Exception as exc:
            logger.error(
                "SemanticCandidateProvider: embedding call failed: %s",
                type(exc).__name__,
            )
            raise ProviderUnavailableError(f"Embedding service call failed: {type(exc).__name__}") from exc

        if query_vec is None:
            raise ProviderUnavailableError("Embedding service returned empty response.")

        # Score scenarios against their prototype vectors
        scored: List[Tuple[ScenarioRoutingProfile, float]] = []
        for profile in profiles:
            proto_vecs = self._prototype_cache.get(profile.scenario_key, [])
            if not proto_vecs:
                continue

            max_sim = max(_cosine_similarity(query_vec, pv) for pv in proto_vecs)
            if max_sim >= self.retrieval_floor:
                scored.append((profile, max_sim))

        # Sort descending by similarity and take top_k
        scored.sort(key=lambda item: item[1], reverse=True)
        top_candidates = scored[: self.top_k]

        evidence: List[RoutingEvidence] = []
        for profile, _ in top_candidates:
            source_ref = f"semantic_provider_v1:{profile.scenario_version}"
            ev_id = compute_evidence_id(
                provider_name=self.name,
                snapshot_hash=snapshot.snapshot_hash,
                candidate_key=profile.scenario_key,
                source=EvidenceSource.semantic,
                polarity=EvidencePolarity.supports,
                strength=EvidenceStrength.weak,
                source_ref=source_ref,
                text_span=None,
            )
            evidence.append(
                RoutingEvidence(
                    id=ev_id,
                    candidate_key=profile.scenario_key,
                    source=EvidenceSource.semantic,
                    polarity=EvidencePolarity.supports,
                    strength=EvidenceStrength.weak,
                    source_ref=source_ref,
                    text_span=None,
                )
            )

        return evidence
