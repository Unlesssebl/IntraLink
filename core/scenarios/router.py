"""Multi-Factor Scenario Router for IntraLink v2 Autopilot.

Scoring factors (weights sum to ≤ 1.0 for deterministic factors; delta applied additively):

  Factor A – Direct Match (scenario domain rules & keyword heuristics)   max 0.50
  Factor B – Catalog-First Prior (ServiceId / TaskTypeId exact mapping)   max 0.20
  Factor C – Extracted Entities bonus (WKS, IP, account presence)         max 0.08
  Factor E – Semantic Prototype Similarity (RAG / BGE-M3 cosine K-NN)    max 0.20
  Factor D – Coherence Guard delta (keyword collision detector)   -0.45 to +0.05

Total deterministic ceiling: 0.50 + 0.20 + 0.08 + 0.20 = 0.98
Default routing threshold: 0.55
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from core.intraservice.dto import TaskDTO
from core.scenarios.base import BaseScenario, ScenarioMatch
from core.scenarios.catalog_prior import CatalogPriorProvider
from core.scenarios.coherence_guard import CoherenceGuard, CoherenceStatus
from core.scenarios.semantic_index import SemanticPrototypeIndex

logger = logging.getLogger("core.scenarios.router")

# Factor weights
WEIGHT_A_DIRECT: float = 0.70       # Domain rules / keyword heuristics (deterministic core)
WEIGHT_B_CATALOG: float = 0.20      # Catalog-First Prior (ServiceId / TaskType)
WEIGHT_C_ENTITIES: float = 0.08     # Extracted entities bonus
WEIGHT_E_SEMANTIC: float = 0.15     # RAG semantic prototype similarity


class ScenarioRouter:
    """Intelligent multi-factor scoring router for autopilot scenarios."""

    def __init__(
        self,
        ai_client: Optional[AsyncOpenAI] = None,
        catalog_prior: Optional[CatalogPriorProvider] = None,
        coherence_guard: Optional[CoherenceGuard] = None,
    ) -> None:
        self.catalog_prior = catalog_prior or CatalogPriorProvider()
        self.coherence_guard = coherence_guard or CoherenceGuard()
        self._semantic_index = SemanticPrototypeIndex(ai_client=ai_client)

    async def warm_up(self, scenarios: Optional[Dict[str, BaseScenario]] = None) -> None:
        """Pre-compute prototype embeddings (call once at application startup)."""
        await self._semantic_index.warm_up(scenarios)

    async def route_task(
        self,
        task: TaskDTO,
        scenarios: Dict[str, BaseScenario],
        threshold: float = 0.50,
    ) -> Optional[Tuple[BaseScenario, ScenarioMatch]]:
        """Score each registered scenario and return the winning (scenario, match) pair."""
        if not scenarios:
            return None

        # Factor B: Catalog Domain Priors
        catalog_priors = self.catalog_prior.get_priors(task)

        # Factor E: Semantic prototype scores
        query_text = f"{task.name or ''} {task.description or ''}".strip()
        semantic_scores: Dict[str, float] = {}
        if self._semantic_index.is_ready:
            try:
                semantic_scores = await self._semantic_index.score(query_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SemanticPrototypeIndex.score() raised unexpectedly: %s", exc)

        scores: List[Tuple[BaseScenario, ScenarioMatch]] = []

        for key, scenario in scenarios.items():
            reasons: List[str] = []
            barriers: List[str] = []
            base_score: float = 0.0

            # Factor E: Semantic Prototype Similarity
            e_score = semantic_scores.get(key, 0.0)

            # Factor A: Direct Match
            direct_match = await scenario.evaluate_match(task, semantic_score=e_score)
            if direct_match.matched:
                a_contrib = direct_match.confidence * WEIGHT_A_DIRECT
                base_score += a_contrib
                reasons.extend(direct_match.reasons)
                reasons.append(f"Factor A: +{a_contrib:.2f} (match conf={direct_match.confidence:.2f})")
            else:
                barriers.extend(direct_match.barriers)

            # Factor B: Catalog Prior
            prior_match = catalog_priors.get(key)
            if prior_match:
                prior_conf, prior_reason = prior_match
                b_contrib = prior_conf * WEIGHT_B_CATALOG
                base_score += b_contrib
                reasons.append(f"Factor B (Catalog Prior): +{b_contrib:.2f} ({prior_reason})")

            # Factor C: Extracted Entities bonus
            if task.entities:
                c_contrib = 0.0
                if key == "install_printer" and (task.entities.pc_name or task.entities.printer_address):
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append(f"Factor C: +{c_contrib:.2f} (workstation/printer IP identified)")
                elif key == "grant_wlan" and (
                    task.applicant_name
                    or task.creator_name
                    or task.applicant_id
                    or (task.entities and task.entities.target_user)
                ):
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append(f"Factor C: +{c_contrib:.2f} (applicant account details identified)")
                elif key == "offline_host" and task.entities.pc_name:
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append(f"Factor C: +{c_contrib:.2f} (target offline PC specified)")
                elif key == "account_create" and (
                    task.entities.last_name or task.entities.first_name or task.entities.user_name
                ):
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append(f"Factor C: +{c_contrib:.2f} (employee identity fields identified)")
                elif key == "account_lock" and (task.entities.target_user or task.entities.user_name):
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append(f"Factor C: +{c_contrib:.2f} (target lock username identified)")
                elif key in ("printer_spooler_restart", "default_printer_fix") and task.entities.pc_name:
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append(f"Factor C: +{c_contrib:.2f} (workstation PC identified)")
                base_score += c_contrib

            # Factor E: Semantic Prototype Similarity
            if e_score > 0.0:
                e_contrib = e_score * WEIGHT_E_SEMANTIC
                base_score += e_contrib
                reasons.append(
                    f"Factor E (Semantic): +{e_contrib:.2f} (cosine={e_score:.2f} vs prototype cluster)"
                )

            # Factor D: Coherence Guard delta
            coherence = self.coherence_guard.evaluate(
                candidate_key=key,
                task=task,
                semantic_scores=semantic_scores,
            )
            base_score += coherence.confidence_delta

            if coherence.status == CoherenceStatus.COHERENT:
                reasons.append(
                    f"Factor D (Coherence): +{coherence.confidence_delta:.2f} ({'; '.join(coherence.reasons)})"
                )
            elif coherence.status == CoherenceStatus.DIVERGENT:
                barriers.append(
                    f"Factor D (Divergence): {coherence.confidence_delta:.2f} ({'; '.join(coherence.reasons)})"
                )

            final_conf = max(0.0, min(1.0, base_score))
            is_divergent = any("factor d (divergence)" in b.lower() for b in barriers)
            is_matched = final_conf >= threshold and not is_divergent

            scores.append((
                scenario,
                ScenarioMatch(
                    scenario_key=key,
                    confidence=round(final_conf, 3),
                    matched=is_matched,
                    reasons=reasons,
                    barriers=barriers,
                ),
            ))

        scores.sort(key=lambda item: item[1].confidence, reverse=True)

        best_scenario, best_match = scores[0]
        if best_match.matched:
            logger.info(
                "Router selected '%s' (conf: %.3f) for ticket #%s",
                best_scenario.scenario_key,
                best_match.confidence,
                task.id,
            )
            return best_scenario, best_match

        # Fallback: RAG consultation ONLY if ticket has genuine consultative intent
        # and no deterministic scenario detected intent (best candidate conf < 0.35)
        if best_match.confidence < 0.35:
            rag_scenario = scenarios.get("rag_consultation")
            if rag_scenario:
                rag_match = await rag_scenario.evaluate_match(task)
                if rag_match.matched:
                    logger.info(
                        "Router fallback to 'rag_consultation' for ticket #%s (no action intent, best=%.3f)",
                        task.id,
                        best_match.confidence,
                    )
                    return rag_scenario, rag_match

        logger.info(
            "Router: no scenario matched ticket #%s (best candidate: '%s' @ %.3f < %.2f threshold)",
            task.id,
            best_match.scenario_key,
            best_match.confidence,
            threshold,
        )
        return None
