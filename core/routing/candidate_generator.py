"""Deterministic CandidateGenerator for Evidence-Based Routing Cascade.

Coordinates parallel execution of CandidateProviders, deduplicates atomic evidence,
enforces cryptographic integrity, and aggregates evidence into ScenarioCandidates
without picking a winner or computing decisions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Sequence, Set

from pydantic import BaseModel, ConfigDict, Field

from core.routing.contracts import (
    EvidencePolarity,
    EvidenceSource,
    RoutingEvidence,
    ScenarioCandidate,
    TicketSnapshot,
)
from core.routing.exceptions import (
    ProviderInvalidResultError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RoutingProviderError,
)
from core.routing.profile_registry import RoutingProfileRegistry
from core.routing.providers.base import CandidateProvider

logger = logging.getLogger("core.routing.candidate_generator")


class CandidateGenerationResult(BaseModel):
    """Immutable result of candidate and evidence generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: List[ScenarioCandidate] = Field(default_factory=list)
    evidence: List[RoutingEvidence] = Field(default_factory=list)
    degraded_providers: Dict[str, str] = Field(default_factory=dict)


class CandidateGenerator:
    """Orchestrator running candidate providers in parallel and assembling candidates."""

    def __init__(
        self,
        registry: RoutingProfileRegistry,
        providers: Sequence[CandidateProvider],
        provider_timeout_seconds: float = 3.0,
    ) -> None:
        self.registry = registry
        self.providers = list(providers)
        self.provider_timeout_seconds = provider_timeout_seconds

    async def generate(self, snapshot: TicketSnapshot) -> CandidateGenerationResult:
        """Run all candidate providers in parallel and assemble candidate hypotheses.

        Guarantees:
        - Independent failure of one provider does not abort others;
        - Providers degraded state is tracked via machine-readable short codes;
        - Zero prompt, ticket, secret, or stacktrace leakage in degradation reasons;
        - Deterministic sorting of candidates and evidence;
        - No decision made, no winner chosen, no fallback created.
        """
        profiles = self.registry.list_all()
        tasks = [
            asyncio.wait_for(
                provider.collect(snapshot, profiles),
                timeout=self.provider_timeout_seconds,
            )
            for provider in self.providers
        ]

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        degraded_providers: Dict[str, str] = {}
        evidence_by_id: Dict[str, RoutingEvidence] = {}
        evidence_provider_source: Dict[str, str] = {}

        for provider, res in zip(self.providers, raw_results, strict=False):
            if isinstance(res, Exception):
                # Map exception to short machine code
                if isinstance(res, (TimeoutError, asyncio.TimeoutError, ProviderTimeoutError)):
                    code = "provider_timeout"
                elif isinstance(res, ProviderUnavailableError):
                    code = "provider_unavailable"
                elif isinstance(res, ProviderInvalidResultError):
                    code = "provider_invalid_result"
                elif isinstance(res, RoutingProviderError):
                    code = getattr(res, "code", "provider_error")
                else:
                    code = "provider_error"

                logger.warning(
                    "CandidateGenerator: provider '%s' degraded with code '%s'",
                    provider.name,
                    code,
                )
                degraded_providers[provider.name] = code
                continue

            if not isinstance(res, list):
                logger.warning(
                    "CandidateGenerator: provider '%s' returned invalid result type '%s'",
                    provider.name,
                    type(res).__name__,
                )
                degraded_providers[provider.name] = "provider_invalid_result"
                continue

            # Process evidence items from healthy provider
            provider_invalid = False
            for item in res:
                if not isinstance(item, RoutingEvidence):
                    provider_invalid = True
                    break

                # Discard evidence referencing unknown scenarios
                if self.registry.get(item.candidate_key) is None:
                    logger.debug(
                        "CandidateGenerator: discarding evidence for unknown scenario '%s'",
                        item.candidate_key,
                    )
                    continue

                if item.id in evidence_by_id:
                    existing = evidence_by_id[item.id]
                    if existing == item:
                        # Exact duplicate - safe deduplication
                        continue
                    else:
                        # Conflicting evidence sharing identical ID with different attributes
                        logger.warning(
                            "CandidateGenerator: conflicting evidence ID '%s' detected from provider '%s'",
                            item.id,
                            provider.name,
                        )
                        provider_invalid = True
                        break
                else:
                    evidence_by_id[item.id] = item
                    evidence_provider_source[item.id] = provider.name

            if provider_invalid:
                degraded_providers[provider.name] = "provider_invalid_result"
                # Purge evidence emitted by this invalid provider
                for eid, p_name in list(evidence_provider_source.items()):
                    if p_name == provider.name:
                        evidence_by_id.pop(eid, None)
                        evidence_provider_source.pop(eid, None)

        # Group valid evidence by candidate scenario_key
        candidate_evidence_map: Dict[str, List[RoutingEvidence]] = {}
        for ev in evidence_by_id.values():
            candidate_evidence_map.setdefault(ev.candidate_key, []).append(ev)

        candidates: List[ScenarioCandidate] = []
        for scen_key, ev_list in candidate_evidence_map.items():
            profile = self.registry.get(scen_key)
            if profile is None:
                continue

            sup_ids: List[str] = []
            con_ids: List[str] = []
            supporting_sources: Set[EvidenceSource] = set()

            for ev in ev_list:
                if ev.polarity == EvidencePolarity.supports:
                    sup_ids.append(ev.id)
                    supporting_sources.add(ev.source)
                elif ev.polarity == EvidencePolarity.contradicts:
                    con_ids.append(ev.id)

            candidates.append(
                ScenarioCandidate(
                    scenario_key=scen_key,
                    scenario_version=profile.scenario_version,
                    evidence_ids=sorted(set(sup_ids)),
                    contradiction_ids=sorted(set(con_ids)),
                    sources=supporting_sources,
                )
            )

        # Deterministic sorting
        candidates.sort(key=lambda c: c.scenario_key)
        sorted_evidence = sorted(evidence_by_id.values(), key=lambda ev: ev.id)

        return CandidateGenerationResult(
            candidates=candidates,
            evidence=sorted_evidence,
            degraded_providers=degraded_providers,
        )
