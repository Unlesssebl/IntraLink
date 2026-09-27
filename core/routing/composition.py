"""Production composition root for Evidence-Based Routing Cascade.

Assembles candidate providers, LLM verifier transport, and routing decision service
sharing a common LiteLLM AsyncOpenAI client.
"""

from __future__ import annotations

import logging
from typing import Optional

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.routing.candidate_generator import CandidateGenerator
from core.routing.cascade import RoutingCascade
from core.routing.persistence import RoutingDecisionRepository, RoutingDecisionService
from core.routing.profile_registry import RoutingProfileRegistry, get_default_profile_registry
from core.routing.providers.catalog import CatalogCandidateProvider
from core.routing.providers.lexical import LexicalCandidateProvider
from core.routing.providers.semantic import SemanticCandidateProvider
from core.routing.verifier.privacy import VerifierPrivacyRouter
from core.routing.verifier.service import GreyZoneVerifier
from core.routing.verifier.transport import LiteLLMVerifierTransport, VerifierTransport

logger = logging.getLogger("core.routing.composition")


_shared_ai_client: Optional[AsyncOpenAI] = None


def get_shared_openai_client() -> AsyncOpenAI:
    """Canonical singleton/shared AsyncOpenAI client for LiteLLM Gateway."""
    global _shared_ai_client
    if _shared_ai_client is None:
        import os

        base_url = os.getenv("LITELLM_BASE_URL", "http://litellm:4000/v1")
        api_key = os.getenv("LITELLM_API_KEY", "sk-intraservice-master-key")
        timeout = float(os.getenv("LITELLM_TIMEOUT", "5.0"))
        max_retries = int(os.getenv("LITELLM_MAX_RETRIES", "1"))
        _shared_ai_client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
    return _shared_ai_client


def create_production_routing_cascade(
    ai_client: Optional[AsyncOpenAI] = None,
    verifier_transport: Optional[VerifierTransport] = None,
    privacy_router: Optional[VerifierPrivacyRouter] = None,
    profile_registry: Optional[RoutingProfileRegistry] = None,
) -> RoutingCascade:
    """Assemble production Evidence-Based Routing Cascade.

    Args:
        ai_client: Shared AsyncOpenAI client connecting to LiteLLM Gateway.
        verifier_transport: Optional override for VerifierTransport (e.g. in tests).
        privacy_router: Optional privacy router for data masking.
        profile_registry: Optional RoutingProfileRegistry override.

    Returns:
        Fully wired, production-ready RoutingCascade instance.
    """
    registry = profile_registry or get_default_profile_registry()
    shared_client = ai_client or get_shared_openai_client()

    # 1. Candidate Providers
    catalog_provider = CatalogCandidateProvider()
    lexical_provider = LexicalCandidateProvider()
    semantic_provider = SemanticCandidateProvider(ai_client=shared_client)

    candidate_generator = CandidateGenerator(
        registry=registry,
        providers=[catalog_provider, lexical_provider, semantic_provider],
    )

    # 2. LLM Grey Zone Verifier
    if verifier_transport is None:
        verifier_transport = LiteLLMVerifierTransport(ai_client=shared_client)

    verifier = GreyZoneVerifier(
        transport=verifier_transport,
        privacy_router=privacy_router or VerifierPrivacyRouter(),
        timeout_seconds=15.0,
    )

    # 3. Assemble Cascade
    return RoutingCascade(
        candidate_generator=candidate_generator,
        verifier=verifier,
        profile_registry=registry,
    )


def create_production_decision_service(
    cascade: Optional[RoutingCascade] = None,
    ai_client: Optional[AsyncOpenAI] = None,
    repository: Optional[RoutingDecisionRepository] = None,
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> RoutingDecisionService:
    """Create RoutingDecisionService configured with production cascade and database repository."""
    active_cascade = cascade or create_production_routing_cascade(ai_client=ai_client)
    return RoutingDecisionService(
        cascade=active_cascade,
        repository=repository or RoutingDecisionRepository(),
        session_factory=session_factory,
    )
