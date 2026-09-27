"""Evidence-Based Routing Cascade orchestrator.

Implements the deterministic cascade flow:
TicketSnapshot -> CandidateGenerator -> GreyZonePolicy -> GreyZoneVerifier -> DecisionPolicy -> FactReadinessPolicy -> RoutingDecision.
"""

from __future__ import annotations

import logging

from core.routing.candidate_generator import CandidateGenerator
from core.routing.contracts import (
    RoutingDecision,
    RoutingState,
    TicketSnapshot,
    VerifierTrace,
)
from core.routing.decision_policy import DecisionPolicy
from core.routing.profile_registry import RoutingProfileRegistry
from core.routing.readiness import FactReadinessPolicy
from core.routing.verifier.contracts import VerifierRunStatus
from core.routing.verifier.service import GreyZoneVerifier

logger = logging.getLogger("core.routing.cascade")

ROUTER_VERSION = "evidence-router-v1"


class RoutingCascade:
    """End-to-end evidence routing cascade producing canonical immutable RoutingDecisions."""

    def __init__(
        self,
        candidate_generator: CandidateGenerator,
        verifier: GreyZoneVerifier | None = None,
        profile_registry: RoutingProfileRegistry | None = None,
        decision_policy: DecisionPolicy | None = None,
        readiness_policy: FactReadinessPolicy | None = None,
    ) -> None:
        if candidate_generator is None:
            raise TypeError("RoutingCascade requires an explicit candidate_generator instance.")
        self._candidate_generator = candidate_generator
        self._profiles = profile_registry or candidate_generator.registry
        self._verifier = verifier
        self._decision_policy = decision_policy or DecisionPolicy(profile_registry=self._profiles)
        self._grey_zone_policy = self._decision_policy.grey_zone_policy
        self._readiness_policy = readiness_policy or FactReadinessPolicy()

    async def decide(self, snapshot: TicketSnapshot) -> RoutingDecision:
        """Execute evidence-based routing pipeline for a point-in-time ticket snapshot."""
        # 1. Candidate and evidence generation via canonical CandidateGenerationResult
        gen_result = await self._candidate_generator.generate(snapshot)
        candidates = gen_result.candidates
        evidence = gen_result.evidence
        degraded_providers = gen_result.degraded_providers
        degraded_components: dict[str, str] = dict(degraded_providers)

        # 2. Evaluate GreyZonePolicy
        outcome = self._grey_zone_policy.evaluate(
            candidates=candidates,
            evidence=evidence,
            degraded_providers=degraded_providers,
        )

        # 3. Direct deterministic selection (no verifier required)
        if outcome.direct_winner is not None:
            winner = outcome.direct_winner.candidate
            reason_code = outcome.direct_winner.reason_code
            state, missing_facts = self._readiness_policy.check_readiness(winner.scenario_key, snapshot)
            status_code = "facts_complete" if state == RoutingState.selected else "missing_required_facts"
            reason_codes = [reason_code, status_code]

            return RoutingDecision(
                task_id=snapshot.task_id,
                snapshot_hash=snapshot.snapshot_hash,
                router_version=ROUTER_VERSION,
                prompt_version=None,
                state=state,
                selected_scenario=winner.scenario_key,
                selected_scenario_version=winner.scenario_version,
                candidates=list(candidates),
                evidence=list(evidence),
                verifier_result=None,
                missing_facts=list(missing_facts),
                degradation_reason=None,
                decision_reason_codes=reason_codes,
                degraded_components=degraded_components,
                verifier_trace=None,
            )

        # 4. Terminal outcome without verifier (unmatched, ambiguous set too broad, degraded)
        if outcome.terminal_state is not None:
            return RoutingDecision(
                task_id=snapshot.task_id,
                snapshot_hash=snapshot.snapshot_hash,
                router_version=ROUTER_VERSION,
                prompt_version=None,
                state=outcome.terminal_state,
                selected_scenario=None,
                selected_scenario_version=None,
                candidates=list(candidates),
                evidence=list(evidence),
                verifier_result=None,
                missing_facts=[],
                degradation_reason=outcome.terminal_degradation_reason,
                decision_reason_codes=list(outcome.terminal_reason_codes),
                degraded_components=degraded_components,
                verifier_trace=None,
            )

        # 5. Grey zone candidate verification via LLM verifier
        eligible_candidates = outcome.eligible_candidates
        if self._verifier is None:
            deg_reason = "verifier_unavailable"
            degraded_components["verifier"] = deg_reason
            return RoutingDecision(
                task_id=snapshot.task_id,
                snapshot_hash=snapshot.snapshot_hash,
                router_version=ROUTER_VERSION,
                prompt_version=None,
                state=RoutingState.degraded,
                selected_scenario=None,
                selected_scenario_version=None,
                candidates=list(candidates),
                evidence=list(evidence),
                verifier_result=None,
                missing_facts=[],
                degradation_reason=deg_reason,
                decision_reason_codes=["verifier_degraded", deg_reason],
                degraded_components=degraded_components,
                verifier_trace=None,
            )

        verifier_run = await self._verifier.verify(
            snapshot=snapshot,
            candidates=eligible_candidates,
            evidence=evidence,
            profiles=self._profiles,
        )

        verifier_trace = VerifierTrace(
            status=verifier_run.status.value,
            prompt_version=verifier_run.prompt_version,
            sanitizer_version=verifier_run.sanitizer_version,
            privacy_zone=verifier_run.privacy_zone.value,
            model_alias=verifier_run.requested_model_alias,
            sanitized_input_hash=verifier_run.sanitized_input_hash,
            degradation_reason=verifier_run.degradation_reason,
        )

        if verifier_run.status == VerifierRunStatus.degraded:
            deg_reason = verifier_run.degradation_reason or "verifier_unavailable"
            degraded_components["verifier"] = deg_reason
            return RoutingDecision(
                task_id=snapshot.task_id,
                snapshot_hash=snapshot.snapshot_hash,
                router_version=ROUTER_VERSION,
                prompt_version=verifier_run.prompt_version,
                state=RoutingState.degraded,
                selected_scenario=None,
                selected_scenario_version=None,
                candidates=list(candidates),
                evidence=list(evidence),
                verifier_result=None,
                missing_facts=[],
                degradation_reason=deg_reason,
                decision_reason_codes=["verifier_degraded", deg_reason],
                degraded_components=degraded_components,
                verifier_trace=verifier_trace,
            )

        # 6. Post-verification arbitration
        arbitration = self._decision_policy.arbitrate_verifier_result(
            verifications=verifier_run.verifications,
            verifier_trace=verifier_trace,
            eligible_candidates=eligible_candidates,
        )

        if arbitration.selected_candidate is not None:
            winner = arbitration.selected_candidate
            state, missing_facts = self._readiness_policy.check_readiness(winner.scenario_key, snapshot)
            status_code = "facts_complete" if state == RoutingState.selected else "missing_required_facts"
            reason_codes = list(arbitration.reason_codes) + [status_code]

            return RoutingDecision(
                task_id=snapshot.task_id,
                snapshot_hash=snapshot.snapshot_hash,
                router_version=ROUTER_VERSION,
                prompt_version=verifier_run.prompt_version,
                state=state,
                selected_scenario=winner.scenario_key,
                selected_scenario_version=winner.scenario_version,
                candidates=list(candidates),
                evidence=list(evidence),
                verifier_result=list(verifier_run.verifications),
                missing_facts=list(missing_facts),
                degradation_reason=None,
                decision_reason_codes=reason_codes,
                degraded_components=degraded_components,
                verifier_trace=verifier_trace,
            )

        # Non-selected post-verification states (ambiguous, unmatched, degraded)
        return RoutingDecision(
            task_id=snapshot.task_id,
            snapshot_hash=snapshot.snapshot_hash,
            router_version=ROUTER_VERSION,
            prompt_version=verifier_run.prompt_version,
            state=arbitration.state,
            selected_scenario=None,
            selected_scenario_version=None,
            candidates=list(candidates),
            evidence=list(evidence),
            verifier_result=list(verifier_run.verifications),
            missing_facts=[],
            degradation_reason=arbitration.degradation_reason,
            decision_reason_codes=list(arbitration.reason_codes),
            degraded_components=degraded_components,
            verifier_trace=verifier_trace,
        )
