"""Multi-Factor Scenario Router for IntraLink v2 Autopilot.

Scoring factors (weights sum to ≤ 1.0 for deterministic factors; delta applied additively):

  Factor A – Direct Match (scenario domain rules & keyword heuristics)   max 0.50
  Factor B – Catalog-First Prior (ServiceId / TaskTypeId exact mapping)   max 0.20
  Factor C – Extracted Entities bonus (WKS, IP, account presence)         max 0.08
  Factor E – Semantic Prototype Similarity (RAG / BGE-M3 cosine K-NN)    max 0.20
  Factor D – Coherence Guard delta (keyword collision detector)   -0.45 to +0.05

Total deterministic ceiling: 0.50 + 0.20 + 0.08 + 0.20 = 0.98
Default routing threshold: 0.55

Factor E replaces fragile regex matching for ambiguous / unseen formulations.
When the SemanticPrototypeIndex is unavailable (cold start, LiteLLM down),
Factor E silently contributes 0.0 — the router degrades gracefully to the
prior four factors without any exceptions.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from core.intraservice.dto import TaskDTO
from worker.src.scenarios.base import BaseScenario, ScenarioMatch
from worker.src.scenarios.catalog_prior import CatalogPriorProvider
from worker.src.scenarios.coherence_guard import CoherenceGuard, CoherenceStatus
from worker.src.scenarios.semantic_index import SemanticPrototypeIndex

logger = logging.getLogger("worker.scenarios.router")

# ---------------------------------------------------------------------------
# Factor weights
# ---------------------------------------------------------------------------
WEIGHT_A_DIRECT: float = 0.50       # Domain rules / keyword heuristics
WEIGHT_B_CATALOG: float = 0.20      # Catalog-First Prior (ServiceId / TaskType)
WEIGHT_C_ENTITIES: float = 0.08     # Extracted entities bonus
WEIGHT_E_SEMANTIC: float = 0.20     # RAG semantic prototype similarity


class ScenarioRouter:
    """Intelligent multi-factor scoring router for autopilot scenarios.

    Constructor is synchronous; call ``await router.warm_up()`` once after
    construction to initialise the semantic index (non-blocking warm-up).
    If ``ai_client`` is None, semantic scoring (Factor E) is disabled.
    """

    def __init__(
        self,
        ai_client: Optional[AsyncOpenAI] = None,
        catalog_prior: Optional[CatalogPriorProvider] = None,
        coherence_guard: Optional[CoherenceGuard] = None,
    ) -> None:
        self.catalog_prior = catalog_prior or CatalogPriorProvider()
        self.coherence_guard = coherence_guard or CoherenceGuard()
        # SemanticPrototypeIndex uses ai_client if provided, otherwise creates default LiteLLM client
        self._semantic_index = SemanticPrototypeIndex(ai_client=ai_client)

    async def warm_up(self, scenarios: Optional[Dict[str, BaseScenario]] = None) -> None:
        """Pre-compute prototype embeddings (call once at application startup).

        Safe to call multiple times; subsequent calls are no-ops.
        """
        await self._semantic_index.warm_up(scenarios)

    async def route_task(
        self,
        task: TaskDTO,
        scenarios: Dict[str, BaseScenario],
        threshold: float = 0.55,
    ) -> Optional[Tuple[BaseScenario, ScenarioMatch]]:
        """Score each registered scenario and return the winning (scenario, match) pair.

        Args:
            task: Incoming ticket DTO.
            scenarios: Registry of active scenario instances keyed by scenario_key.
            threshold: Minimum confidence required to declare a match (default 0.55).

        Returns:
            ``(BaseScenario, ScenarioMatch)`` for the highest-confidence match, or
            ``None`` if no scenario clears the threshold.
        """
        if not scenarios:
            return None

        # -------------------------------------------------------------------
        # Factor B: Obtain Catalog Prior (computed once, applied per-scenario)
        # -------------------------------------------------------------------
        prior_res = self.catalog_prior.get_prior(task)
        prior_key: Optional[str] = None
        prior_conf: float = 0.0
        prior_reason: str = ""
        if prior_res:
            prior_key, prior_conf, prior_reason = prior_res

        # -------------------------------------------------------------------
        # Factor E: Semantic prototype scores (one query embedding per ticket)
        # -------------------------------------------------------------------
        query_text = f"{task.name or ''} {task.description or ''}".strip()
        semantic_scores: Dict[str, float] = {}
        if self._semantic_index.is_ready:
            try:
                semantic_scores = await self._semantic_index.score(query_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SemanticPrototypeIndex.score() raised unexpectedly: %s", exc)

        # -------------------------------------------------------------------
        # Main scoring loop
        # -------------------------------------------------------------------
        scores: List[Tuple[BaseScenario, ScenarioMatch]] = []

        for key, scenario in scenarios.items():
            reasons: List[str] = []
            barriers: List[str] = []
            base_score: float = 0.0

            # Factor E: Semantic Prototype Similarity                       max 0.20
            e_score = semantic_scores.get(key, 0.0)

            # Factor A: Direct Match (domain rules & keyword heuristics + semantic prototype)  max 0.50
            direct_match = await scenario.evaluate_match(task, semantic_score=e_score)
            if direct_match.matched:
                a_contrib = direct_match.confidence * WEIGHT_A_DIRECT
                base_score += a_contrib
                reasons.extend(direct_match.reasons)
                reasons.append(f"Factor A: +{a_contrib:.2f} (match conf={direct_match.confidence:.2f})")
            else:
                barriers.extend(direct_match.barriers)

            # Factor B: Catalog Prior                                       max 0.20
            if key == prior_key:
                b_contrib = prior_conf * WEIGHT_B_CATALOG
                base_score += b_contrib
                reasons.append(f"Factor B (Catalog Prior): +{b_contrib:.2f} ({prior_reason})")

            # Factor C: Extracted Entities bonus                            max 0.08
            if task.entities:
                c_contrib = 0.0
                if key == "install_printer" and (task.entities.pc_name or task.entities.printer_address):
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append("Factor C: +{:.2f} (workstation/printer IP identified)".format(c_contrib))
                elif key in ("ad_password_reset", "grant_wlan") and (
                    task.applicant_name
                    or task.creator_name
                    or task.applicant_id
                    or (task.entities and task.entities.target_user)
                ):
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append("Factor C: +{:.2f} (applicant account details identified)".format(c_contrib))
                elif key == "offline_host" and task.entities.pc_name:
                    c_contrib = WEIGHT_C_ENTITIES
                    reasons.append("Factor C: +{:.2f} (target offline PC specified)".format(c_contrib))
                base_score += c_contrib

            # Factor E: Semantic Prototype Similarity                       max 0.20
            e_score = semantic_scores.get(key, 0.0)
            if e_score > 0.0:
                e_contrib = e_score * WEIGHT_E_SEMANTIC
                base_score += e_contrib
                reasons.append(
                    "Factor E (Semantic): +{:.2f} (cosine={:.2f} vs prototype cluster)".format(e_contrib, e_score)
                )

            # Factor D: Coherence Guard delta                       -0.45 to +0.05
            coherence = self.coherence_guard.evaluate(
                candidate_key=key,
                task=task,
                semantic_scores=semantic_scores,
            )
            base_score += coherence.confidence_delta

            if coherence.status == CoherenceStatus.COHERENT:
                reasons.append(
                    "Factor D (Coherence): +{:.2f} ({})".format(
                        coherence.confidence_delta, "; ".join(coherence.reasons)
                    )
                )
            elif coherence.status == CoherenceStatus.DIVERGENT:
                barriers.append(
                    "Factor D (Divergence): {:.2f} ({})".format(
                        coherence.confidence_delta, "; ".join(coherence.reasons)
                    )
                )

            final_conf = max(0.0, min(1.0, base_score))
            # A divergence penalty is a hard blocker regardless of accumulated score
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

        # Sort by confidence descending
        scores.sort(key=lambda item: item[1].confidence, reverse=True)

        for _, match in scores:
            logger.debug(
                "Router candidate '%s': score=%.3f, matched=%s (factors: %s)",
                match.scenario_key,
                match.confidence,
                match.matched,
                [r for r in match.reasons if r.startswith("Factor")],
            )

        # Return best candidate if it clears threshold
        best_scenario, best_match = scores[0]
        if best_match.matched:
            logger.info(
                "Router selected '%s' (conf: %.3f) for ticket #%s",
                best_scenario.scenario_key,
                best_match.confidence,
                task.id,
            )
            return best_scenario, best_match

        # Fallback: RAG consultation if no deterministic scenario matched
        rag_scenario = scenarios.get("rag_consultation")
        if rag_scenario:
            rag_match = await rag_scenario.evaluate_match(task)
            if rag_match.matched:
                logger.info(
                    "Router fallback to 'rag_consultation' for ticket #%s (no deterministic match, best=%.3f)",
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
