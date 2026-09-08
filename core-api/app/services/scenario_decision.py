"""Application service that creates a unified decision envelope."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from shared.domain import (
    CandidateOutcome,
    ClarificationRequired,
    DecisionEnvelope,
    DecisionOutcomeAdapter,
    FactObservation,
    FactState,
)

from app.services.decision_compiler import DecisionCompiler, outcome_evidence_refs
from app.services.facts import merge_observations
from app.services.resolution_service import ResolutionUnavailable
from app.services.scenario_pipeline import (
    FactCollectionPlanner,
    PolicyResolver,
    ResponseComposer,
    ScenarioRouter,
)
from app.services.scenarios import ScenarioContext, get_scenario_registry


class ScenarioDecisionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.registry = get_scenario_registry()
        self.compiler = DecisionCompiler()
        self.router = ScenarioRouter(self.registry)
        self.fact_planner = FactCollectionPlanner()
        self.policy_resolver = PolicyResolver(db)
        self.response_composer = ResponseComposer()

    async def analyze(
        self,
        *,
        task: dict[str, Any],
        comments: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
        legacy_decision: dict[str, Any] | None = None,
        generated_response: str | None = None,
        fact_revision: int = 0,
        decision_id: str | None = None,
        decision_version: int = 1,
        observations: list[FactObservation] | None = None,
        pinned_scenario_key: str | None = None,
        pinned_scenario_version: int | None = None,
    ) -> DecisionEnvelope:
        if observations is None:
            observations = await self.fact_planner.collect(
                task, comments=comments, diagnostics=diagnostics
            )
        facts = merge_observations(observations, revision=fact_revision)
        scenario_task = deepcopy(task)
        person_keys = (
            "surname",
            "name",
            "patronymic",
            "company",
            "department",
            "title",
            "phone",
            "pc_name",
        )
        person = {
            key: facts.valid_value(key, "")
            for key in person_keys
            if facts.valid_value(key, "") != ""
        }
        if person:
            scenario_task["_extracted_person"] = person
        for key, target in (
            ("pc_name", "_extracted_pc_name"),
            ("printer_address", "_extracted_printer_address"),
            ("file_path", "_extracted_file_path"),
            ("clarification_answer", "_extracted_clarification_answer"),
        ):
            value = facts.valid_value(key)
            if value not in (None, ""):
                scenario_task[target] = value
        context = ScenarioContext(
            task=scenario_task,
            facts=facts,
            diagnostics=diagnostics,
            kb_matches=kb_matches or [],
            comments=comments or [],
        )
        scenario = self.router.route(
            context,
            pinned_key=pinned_scenario_key,
            pinned_version=pinned_scenario_version,
        )
        requirements = scenario.requirements(context)
        missing = [
            requirement.key
            for requirement in requirements
            if facts.facts.get(requirement.key) is None
            or facts.facts[requirement.key].state is FactState.MISSING
        ]
        invalid = [
            requirement.key
            for requirement in requirements
            if facts.facts.get(requirement.key) is not None
            and facts.facts[requirement.key].state
            in {FactState.INVALID, FactState.AMBIGUOUS, FactState.CONFLICTING, FactState.STALE}
        ]

        if missing or invalid:
            outcome = ClarificationRequired(
                rule_key=f"scenario.{scenario.definition.key}",
                rule_version=str(scenario.definition.version),
                outcome_key=(
                    scenario.definition.clarification_outcome_key
                    or "manual_clarification_required"
                ),
                missing_fields=missing,
                invalid_fields=invalid,
                context={
                    **{
                        key: str(facts.valid_value(key))
                        for key in facts.facts
                        if facts.valid_value(key) not in (None, "")
                    },
                    "invalid_fields": ", ".join(sorted(set(missing + invalid))),
                },
            )
        elif legacy_decision and isinstance(legacy_decision.get("typed_outcome"), dict):
            outcome = DecisionOutcomeAdapter.validate_python(legacy_decision["typed_outcome"])
        else:
            outcome = scenario.decide(context)

        candidate = CandidateOutcome(
            candidate_id=f"scenario:{scenario.definition.key}:{scenario.definition.version}",
            source=(
                "rag"
                if scenario.definition.key == "rag_consultation"
                else "diagnostic"
                if scenario.definition.key == "offline_host" and diagnostics
                else "rule"
            ),
            outcome=outcome,
            evidence_refs=[],
            score=0.95 if scenario.definition.risk_level >= 2 else 0.85,
            can_authorize_action=scenario.definition.key != "rag_consultation",
        )
        candidate.evidence_refs = outcome_evidence_refs(candidate)

        policy: dict[str, Any] = {}
        response_draft = generated_response or ""
        outcome_key = getattr(outcome, "outcome_key", None)
        kind = getattr(outcome, "kind", None)
        if outcome_key and kind in {"clarification", "action", "resolution"}:
            try:
                policy = await self.policy_resolver.resolve(outcome)
                response_draft = self.response_composer.compose(
                    scenario_risk=scenario.definition.risk_level,
                    outcome=outcome,
                    policy=policy,
                    generated_response=response_draft,
                )
            except ResolutionUnavailable as exc:
                policy = {"resolution_error": str(exc), "requires_approval": False}
                response_draft = (
                    "Решение требует ручной проверки: конфигурация ответа недоступна."
                )

        return await self.compiler.compile(
            scenario_key=scenario.definition.key,
            scenario_version=scenario.definition.version,
            scenario_risk=scenario.definition.risk_level,
            allowed_actions=set(scenario.definition.allowed_actions),
            facts=facts,
            candidates=[candidate],
            policy=policy,
            response_draft=response_draft,
            decision_id=decision_id,
            decision_version=decision_version,
            service_id=task.get("ServiceId") or task.get("service_id"),
        )
