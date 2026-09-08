"""Evidence-based selection and verification of scenario outcomes."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from shared.domain import (
    ActionProposed,
    CandidateOutcome,
    DecisionEnvelope,
    FactBag,
    FactSensitivity,
    FactState,
    SynthesisProposal,
)

from app.services.ai.hub import ai_hub
from app.services.ai.schemas import RoutedInferenceRequest, RoutingMetadata


Adjudicator = Callable[
    [list[CandidateOutcome], list[str], int | None], Awaitable[SynthesisProposal | None]
]


def evidence_ref(source: str, field: str, code: str) -> str:
    return f"{source}:{field}:{code}"


def outcome_evidence_refs(candidate: CandidateOutcome) -> list[str]:
    refs = list(candidate.evidence_refs)
    refs.extend(
        evidence_ref(item.source, item.field, item.code)
        for item in candidate.outcome.evidence
    )
    return list(dict.fromkeys(refs))


def redacted_fact_summary(facts: FactBag) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, fact in facts.facts.items():
        if fact.state is not FactState.VALID:
            result[key] = {"state": fact.state.value}
            continue
        selected = next(
            (
                item
                for item in fact.observations
                if item.source == fact.selected_source
                and item.source_ref == fact.selected_source_ref
            ),
            None,
        )
        sensitivity = selected.sensitivity if selected else FactSensitivity.INTERNAL
        if sensitivity is FactSensitivity.SECRET:
            continue
        result[key] = {
            "state": fact.state.value,
            "value": "<redacted>"
            if sensitivity is FactSensitivity.PERSONAL
            else fact.value,
            "source": fact.selected_source.value if fact.selected_source else None,
            "source_ref": fact.selected_source_ref,
        }
    return result


async def routed_llm_adjudicator(
    candidates: list[CandidateOutcome],
    available_evidence: list[str],
    service_id: int | None,
) -> SynthesisProposal | None:
    schema = SynthesisProposal.model_json_schema()
    payload = {
        "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        "available_evidence_refs": available_evidence,
        "constraints": [
            "Select only candidate_id supplied in candidates",
            "Use only supplied evidence refs",
            "Never invent an action or status",
            "Abstain by listing ambiguities when evidence conflicts",
        ],
    }
    response = await ai_hub.dispatch_routed_inference(
        RoutedInferenceRequest(
            prompt=json.dumps(payload, ensure_ascii=False),
            system_prompt=(
                "Ты компилируешь уже допустимые варианты Helpdesk в JSON Schema. "
                "Не создавай новые факты, статусы или действия."
            ),
            metadata=RoutingMetadata(service_id=service_id),
            temperature=0.0,
            response_schema=schema,
        )
    )
    if response is None:
        return None
    try:
        return SynthesisProposal.model_validate_json(response.text)
    except ValueError:
        return None


class DecisionCompiler:
    def __init__(self, adjudicator: Adjudicator | None = routed_llm_adjudicator):
        self.adjudicator = adjudicator

    @staticmethod
    def _eligible(
        candidates: list[CandidateOutcome], allowed_actions: set[str]
    ) -> list[CandidateOutcome]:
        eligible: list[CandidateOutcome] = []
        for candidate in candidates:
            if isinstance(candidate.outcome, ActionProposed):
                if candidate.source == "rag" or not candidate.can_authorize_action:
                    continue
                if candidate.outcome.action not in allowed_actions:
                    continue
                if not outcome_evidence_refs(candidate):
                    continue
            eligible.append(candidate)
        return eligible

    @staticmethod
    def _confidence(selected: CandidateOutcome, facts: FactBag) -> float:
        states = [fact.state for fact in facts.facts.values()]
        completeness = (
            sum(state is FactState.VALID for state in states) / len(states) if states else 0.0
        )
        conflicts = sum(
            state in {FactState.CONFLICTING, FactState.INVALID} for state in states
        )
        grounded = 1.0 if outcome_evidence_refs(selected) else 0.0
        return max(
            0.0,
            min(0.99, selected.score * 0.6 + completeness * 0.3 + grounded * 0.1 - conflicts * 0.2),
        )

    @staticmethod
    def _validate_proposal(
        proposal: SynthesisProposal,
        candidates: list[CandidateOutcome],
        available_evidence: set[str],
    ) -> CandidateOutcome | None:
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.candidate_id == proposal.selected_candidate_id
            ),
            None,
        )
        if selected is None:
            return None
        if not set(proposal.used_evidence_refs).issubset(available_evidence):
            return None
        return selected

    async def compile(
        self,
        *,
        scenario_key: str,
        scenario_version: int,
        scenario_risk: int,
        allowed_actions: set[str],
        facts: FactBag,
        candidates: list[CandidateOutcome],
        policy: dict[str, Any] | None = None,
        response_draft: str = "",
        decision_id: str | None = None,
        decision_version: int = 1,
        service_id: int | None = None,
    ) -> DecisionEnvelope:
        eligible = self._eligible(candidates, allowed_actions)
        if not eligible:
            raise ValueError("no_eligible_decision_candidate")
        eligible.sort(key=lambda candidate: candidate.score, reverse=True)
        selected = eligible[0]
        all_evidence = {
            ref for candidate in eligible for ref in outcome_evidence_refs(candidate)
        }

        # High-risk scenarios are never adjudicated by a model.
        if scenario_risk <= 1 and len(eligible) > 1 and self.adjudicator is not None:
            proposal = await self.adjudicator(eligible, sorted(all_evidence), service_id)
            if proposal is not None and not proposal.ambiguities:
                verified = self._validate_proposal(proposal, eligible, all_evidence)
                if verified is not None:
                    selected = verified

        resolved_policy = policy or {}
        return DecisionEnvelope(
            decision_id=decision_id,
            decision_version=decision_version,
            scenario_key=scenario_key,
            scenario_version=scenario_version,
            facts_revision=facts.revision,
            facts_summary=redacted_fact_summary(facts),
            candidates=eligible,
            outcome=selected.outcome,
            policy=resolved_policy,
            response_draft=response_draft,
            evidence_refs=outcome_evidence_refs(selected),
            confidence=self._confidence(selected, facts),
            requires_approval=bool(resolved_policy.get("requires_approval")),
            status=(
                "waiting_approval"
                if resolved_policy.get("requires_approval")
                else "proposed"
            ),
        )
