"""Application service that creates a unified decision envelope."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from shared.domain import (
    CandidateOutcome,
    ClarificationRequired,
    DecisionEnvelope,
    FactObservation,
    FactState,
)

from app.services.ai.hub import ai_hub
from app.services.ai.schemas import RoutedInferenceRequest, RoutingMetadata
from app.services.decision_compiler import (
    DecisionCompiler,
    outcome_evidence_refs,
    redacted_fact_summary,
)
from app.services.facts import merge_observations
from app.services.response_guard import GeneratedResponse, guarded_response
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

    @staticmethod
    async def _generate_low_risk_response(
        *,
        policy_comment: str,
        outcome_kind: str,
        facts_summary: dict[str, Any],
        evidence_refs: list[str],
        service_id: int | None,
    ) -> GeneratedResponse | None:
        safe_facts = {
            key: value
            for key, value in facts_summary.items()
            if isinstance(value, dict)
            and value.get("state") == "valid"
            and value.get("value") not in (None, "", "<redacted>")
        }
        payload = {
            "policy_text": policy_comment,
            "outcome_kind": outcome_kind,
            "confirmed_facts": safe_facts,
            "allowed_evidence_refs": evidence_refs,
            "constraints": [
                "Rewrite only the supplied policy text in concise professional Russian",
                "Do not claim that an action was completed or checked",
                "Do not add identifiers, technical values, instructions, or historical cases",
                "Return only JSON matching the schema",
            ],
        }
        try:
            response = await ai_hub.dispatch_routed_inference(
                RoutedInferenceRequest(
                    prompt=json.dumps(payload, ensure_ascii=False),
                    system_prompt=(
                        "Ты редактируешь безопасный готовый текст Helpdesk. "
                        "Не добавляй факты и не меняй смысл решения."
                    ),
                    metadata=RoutingMetadata(service_id=service_id),
                    temperature=0.0,
                    max_tokens=800,
                    response_schema=GeneratedResponse.model_json_schema(),
                )
            )
            if response is None:
                return None
            return GeneratedResponse.model_validate_json(response.text)
        except (ValueError, TypeError):
            return None

    async def analyze(
        self,
        *,
        task: dict[str, Any],
        comments: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
        kb_matches: list[dict[str, Any]] | None = None,
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
        else:
            # Callers that provide observations own collection and ordering.  This
            # keeps one decision bound to one immutable fact snapshot.
            observations = list(observations)
        facts = merge_observations(observations, revision=fact_revision)
        printer_targets = facts.valid_value("printer_targets", [])
        printer_address = facts.valid_value("printer_address")
        if isinstance(printer_targets, list) and printer_address:
            for target in printer_targets:
                if (
                    isinstance(target, dict)
                    and not target.get("printer_address")
                    and target.get("connection_type") != "usb"
                ):
                    target["printer_address"] = printer_address
                    target["connection_type"] = "network"
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
            ("printer_name", "_extracted_printer_name"),
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
        if scenario.definition.key == "install_printer":
            printer_targets = facts.valid_value("printer_targets", [])
            if isinstance(printer_targets, list) and any(
                isinstance(target, dict)
                and target.get("connection_type") != "usb"
                and not target.get("printer_address")
                for target in printer_targets
            ):
                invalid.append("printer_targets")

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
        outcome_key = getattr(outcome, "outcome_key", None)
        kind = getattr(outcome, "kind", None)
        if outcome_key and kind in {"clarification", "action", "resolution"}:
            try:
                policy = await self.policy_resolver.resolve(outcome)
            except ResolutionUnavailable as exc:
                policy = {"resolution_error": str(exc), "requires_approval": False}
        facts_summary = redacted_fact_summary(facts)
        policy_comment = str(policy.get("comment") or "").strip()
        generated = None
        if scenario.definition.risk_level <= 1 and policy_comment:
            generated = await self._generate_low_risk_response(
                policy_comment=policy_comment,
                outcome_kind=str(kind or "manual_review"),
                facts_summary=facts_summary,
                evidence_refs=candidate.evidence_refs,
                service_id=task.get("ServiceId") or task.get("service_id"),
            )
        response = guarded_response(
            generated=generated,
            template_text=policy_comment,
            allowed_evidence_refs=candidate.evidence_refs,
            facts_summary=facts_summary,
        )

        return await self.compiler.compile(
            scenario_key=scenario.definition.key,
            scenario_version=scenario.definition.version,
            scenario_risk=scenario.definition.risk_level,
            allowed_actions=set(scenario.definition.allowed_actions),
            facts=facts,
            candidates=[candidate],
            policy=policy,
            response=response,
            decision_id=decision_id or str(uuid.uuid4()),
            decision_version=decision_version,
            service_id=task.get("ServiceId") or task.get("service_id"),
        )
