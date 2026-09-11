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
    DecisionRoutingInfo,
    ExecutionPlan,
    FactObservation,
    FactState,
    ManualReviewRequired,
    ResponseProvenance,
)

from app.services.ai.hub import ai_hub
from app.services.ai.schemas import (
    InferencePurpose,
    RoutedInferenceRequest,
    RoutingMetadata,
)
from app.services.response_context import ResponseContext, ResponseContextBuilder
from app.services.decision_compiler import (
    DecisionCompiler,
    outcome_evidence_refs,
    redacted_fact_summary,
)
from app.services.facts import merge_observations
from app.services.plan_builder import PlanBuilder
from app.services.response_guard import GeneratedResponse, guarded_response
from app.services.resolution_service import ResolutionUnavailable
from app.services.scenario_pipeline import (
    FactCollectionPlanner,
    PolicyResolver,
    ResponseComposer,
    ScenarioRouter,
)
from app.services.decision_trace import DecisionExecutionTrace
from app.services.scenarios import ScenarioContext, get_scenario_registry


def build_internal_summary(
    *,
    scenario_key: str,
    scenario_version: int,
    facts_summary: dict[str, Any],
    diagnostics: dict[str, Any] | None,
    plan: ExecutionPlan | None,
    task: dict[str, Any],
) -> str:
    lines: list[str] = []
    task_id = task.get("id") or task.get("task_id") or task.get("Id")
    prefix = f"Инженерная сводка по заявке #{task_id}" if task_id else "Инженерная сводка"
    lines.append(prefix)
    lines.append(f"Сценарий: {scenario_key} (v{scenario_version})")

    key_params: list[str] = []
    for param_name in (
        "pc_name",
        "printer_name",
        "printer_address",
        "file_path",
        "connection_type",
        "device_type",
    ):
        val = facts_summary.get(param_name, {})
        if isinstance(val, dict) and val.get("value") and val.get("value") != "<redacted>":
            key_params.append(f"{param_name}: {val['value']}")
    if key_params:
        lines.append("Параметры: " + ", ".join(key_params))

    if diagnostics:
        diag_parts: list[str] = []
        host = diagnostics.get("host") or diagnostics.get("pc_name")
        if host:
            diag_parts.append(f"хост {host}")
        if "ping" in diagnostics or "ping_ok" in diagnostics:
            ping_val = diagnostics.get("ping_ok", diagnostics.get("ping"))
            diag_parts.append(f"ping: {'OK' if ping_val else 'FAIL'}")
        if "smb_ok" in diagnostics or "smb_445" in diagnostics:
            smb_val = diagnostics.get("smb_ok", diagnostics.get("smb_445"))
            diag_parts.append(f"SMB(445): {'OK' if smb_val else 'FAIL'}")
        if "winrm_ok" in diagnostics or "winrm_5985" in diagnostics:
            winrm_val = diagnostics.get("winrm_ok", diagnostics.get("winrm_5985"))
            diag_parts.append(f"WinRM(5985): {'OK' if winrm_val else 'FAIL'}")
        if diag_parts:
            lines.append("Диагностика: " + ", ".join(diag_parts))

    if plan:
        lines.append(
            f"Фаза плана: {plan.phase}. Следующее действие: {plan.next_action_description}"
        )
        step_lines = []
        for s in plan.steps:
            mark = "x" if s.status.value == "completed" else "-" if s.status.value == "skipped" else " "
            step_lines.append(f"  [{mark}] {s.title}")
        if step_lines:
            lines.append("Шаги плана:\n" + "\n".join(step_lines))

    return "\n".join(lines)


class ScenarioDecisionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        compiler: DecisionCompiler | None = None,
        router: ScenarioRouter | None = None,
        fact_planner: FactCollectionPlanner | None = None,
        policy_resolver: PolicyResolver | None = None,
        response_composer: ResponseComposer | None = None,
        ai_enabled: bool = True,
    ):
        self.db = db
        self.registry = get_scenario_registry()
        self.compiler = compiler or DecisionCompiler()
        self.router = router or ScenarioRouter(self.registry)
        self.fact_planner = fact_planner or FactCollectionPlanner()
        self.policy_resolver = policy_resolver or PolicyResolver(db)
        self.response_composer = response_composer or ResponseComposer()
        self.ai_enabled = ai_enabled

    @staticmethod
    async def _generate_low_risk_response(
        *,
        response_context: ResponseContext,
        service_id: int | None,
    ) -> tuple[GeneratedResponse | None, ResponseProvenance]:
        variants = ResponseContextBuilder.build_prompt_variants(
            response_context, tone="default"
        )
        primary = variants[1] if len(variants) > 1 else variants[0]
        try:
            inference_resp = await ai_hub.dispatch_routed_inference(
                RoutedInferenceRequest(
                    prompt=primary.prompt,
                    system_prompt=primary.system_prompt,
                    metadata=RoutingMetadata(service_id=service_id),
                    temperature=0.0,
                    max_tokens=primary.max_tokens,
                    response_schema=GeneratedResponse.model_json_schema(),
                    prompt_variants=variants,
                    purpose=InferencePurpose.RESPONSE_DEFAULT,
                )
            )
            if inference_resp is None:
                provenance = ResponseProvenance(
                    source="fallback_template",
                    requested_backend="litellm_gemini",
                    fallback_used=True,
                    fallback_reason_code="all_providers_failed",
                    rag_candidate_count=len(response_context.rag_candidates),
                    rag_used_count=0,
                )
                return None, provenance

            circuit_val = (
                inference_resp.circuit.value
                if hasattr(inference_resp.circuit, "value")
                else str(inference_resp.circuit)
            )
            provenance = ResponseProvenance(
                source="llm" if not inference_resp.fallback_used else "fallback_template",
                requested_backend=inference_resp.requested_backend,
                actual_backend=inference_resp.actual_backend,
                model_alias=inference_resp.model_alias,
                resolved_model=inference_resp.resolved_model,
                circuit=circuit_val if circuit_val in ("red", "yellow", "green") else "unknown",
                context_profile=inference_resp.context_profile,
                tone="default",
                fallback_used=inference_resp.fallback_used,
                fallback_reason_code=inference_resp.fallback_reason_code,
                rag_candidate_count=len(response_context.rag_candidates),
                rag_used_count=len(inference_resp.rag_refs),
                rag_refs=inference_resp.rag_refs,
                attempts=inference_resp.attempts,
                duration_ms=int(inference_resp.execution_time_ms),
            )
            try:
                generated = GeneratedResponse.model_validate_json(inference_resp.text)
                return generated, provenance
            except Exception:
                provenance.source = "fallback_template"
                provenance.fallback_used = True
                provenance.fallback_reason_code = "invalid_schema"
                return None, provenance
        except Exception:
            provenance = ResponseProvenance(
                source="fallback_template",
                fallback_used=True,
                fallback_reason_code="all_providers_failed",
                rag_candidate_count=len(response_context.rag_candidates),
            )
            return None, provenance

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
        trace: DecisionExecutionTrace | None = None,
        rag_metadata: dict[str, Any] | None = None,
    ) -> DecisionEnvelope:
        facts_span = (
            trace.start_span(
                "facts",
                input_data={
                    "fact_revision": fact_revision,
                    "provided_observations": observations is not None,
                    "task_id": task.get("Id") or task.get("id"),
                },
            )
            if trace
            else None
        )
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

        if facts_span:
            facts_span.finish(
                status="succeeded",
                output_data={
                    "valid_facts_count": sum(
                        1 for f in facts.facts.values() if f.state is FactState.VALID
                    ),
                    "missing_facts_count": sum(
                        1 for f in facts.facts.values() if f.state is FactState.MISSING
                    ),
                    "invalid_facts_count": sum(
                        1
                        for f in facts.facts.values()
                        if f.state
                        in {
                            FactState.INVALID,
                            FactState.AMBIGUOUS,
                            FactState.CONFLICTING,
                            FactState.STALE,
                        }
                    ),
                    "total_observations": len(observations),
                },
                metadata={
                    "fact_revision": fact_revision,
                    "sources": sorted(list({o.source for o in observations})),
                },
            )

        # 2. RAG step
        if trace:
            rag_origin = (rag_metadata or {}).get(
                "origin", "provided" if rag_metadata is None else "live"
            )
            rag_duration = (rag_metadata or {}).get("duration_ms")
            rag_status = (rag_metadata or {}).get("status", "succeeded")
            rag_err = (rag_metadata or {}).get("error_code")
            trace.record_step(
                component="rag",
                status=rag_status,
                input_data={"query": (rag_metadata or {}).get("query")},
                output_data={
                    "matches_count": len(kb_matches or []),
                    "top_score": (
                        kb_matches[0].get("similarity") if kb_matches else None
                    ),
                    "precedents": [
                        m.get("task_id") or m.get("id")
                        for m in (kb_matches or [])[:5]
                    ],
                },
                metadata={
                    "origin": rag_origin,
                    "service_id": task.get("ServiceId") or task.get("service_id"),
                },
                error_code=rag_err,
                duration_ms=rag_duration,
            )

        context = ScenarioContext(
            task=scenario_task,
            facts=facts,
            diagnostics=diagnostics,
            kb_matches=kb_matches or [],
            comments=comments or [],
        )

        # 3. Routing step
        routing_span = (
            trace.start_span(
                "routing",
                input_data={
                    "pinned_key": pinned_scenario_key,
                    "pinned_version": pinned_scenario_version,
                },
            )
            if trace
            else None
        )
        route_res = self.router.route_result(
            context,
            pinned_key=pinned_scenario_key,
            pinned_version=pinned_scenario_version,
        )
        scenario = route_res.scenario
        if routing_span:
            routing_span.finish(
                status="succeeded",
                output_data={
                    "selected_scenario": scenario.definition.key,
                    "scenario_version": scenario.definition.version,
                    "score": route_res.score,
                    "runner_up_score": route_res.runner_up_score,
                    "is_ambiguous": route_res.is_ambiguous,
                    "reasons": route_res.reasons,
                },
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

        if route_res.is_ambiguous:
            outcome = ManualReviewRequired(
                rule_key=f"scenario.{scenario.definition.key}",
                rule_version=str(scenario.definition.version),
                reason=f"Ambiguous scenario match between candidates: {', '.join(route_res.reasons)}",
            )
        elif missing or invalid:
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
            score=route_res.score,
            can_authorize_action=scenario.definition.key != "rag_consultation" and not route_res.is_ambiguous,
            routing=DecisionRoutingInfo(
                selected_score=route_res.score,
                runner_up_score=route_res.runner_up_score,
                reasons=route_res.reasons,
                is_ambiguous=route_res.is_ambiguous,
            ),
        )
        candidate.evidence_refs = outcome_evidence_refs(candidate)

        # 4. Policy step
        policy_span = (
            trace.start_span(
                "policy",
                input_data={
                    "outcome_kind": getattr(outcome, "kind", None),
                    "outcome_key": getattr(outcome, "outcome_key", None),
                },
            )
            if trace
            else None
        )
        policy: dict[str, Any] = {}
        outcome_key = getattr(outcome, "outcome_key", None)
        kind = getattr(outcome, "kind", None)
        policy_err = None
        if outcome_key and kind in {"clarification", "action", "resolution"}:
            try:
                policy = await self.policy_resolver.resolve(outcome)
            except ResolutionUnavailable as exc:
                policy = {"resolution_error": str(exc), "requires_approval": False}
                policy_err = "resolution_policy_unavailable"
        if policy_span:
            policy_span.finish(
                status="fallback" if policy.get("resolution_error") else "succeeded",
                output_data={
                    "status_id": policy.get("status_id") or policy.get("target_status_id"),
                    "requires_approval": policy.get("requires_approval", False),
                    "has_error": bool(policy.get("resolution_error")),
                },
                error_code=policy_err,
            )

        facts_summary = redacted_fact_summary(facts)
        policy_comment = str(policy.get("comment") or "").strip()

        # 5. Plan step
        plan_span = (
            trace.start_span("plan", input_data={"scenario_key": scenario.definition.key})
            if trace
            else None
        )
        plan = PlanBuilder.build(
            scenario_key=scenario.definition.key,
            scenario_version=scenario.definition.version,
            facts=facts,
            diagnostics=diagnostics,
        )
        if plan_span:
            plan_span.finish(
                status="succeeded",
                output_data={
                    "phase": plan.phase if plan else None,
                    "next_action": plan.next_action_description if plan else None,
                    "public_steps_count": len(plan.public_steps) if plan else 0,
                    "internal_steps_count": len(plan.internal_steps) if plan else 0,
                },
            )

        response_ctx = ResponseContextBuilder.build_context(
            decision_id=decision_id or str(uuid.uuid4()),
            decision_version=decision_version,
            scenario_key=scenario.definition.key,
            scenario_version=scenario.definition.version,
            outcome_kind=str(kind or "manual_review"),
            policy_comment=policy_comment,
            facts_summary=facts_summary,
            evidence_refs=candidate.evidence_refs,
            plan=plan,
            kb_matches=kb_matches,
        )

        # 6. AI step
        ai_span = (
            trace.start_span(
                "ai",
                input_data={
                    "ai_enabled": self.ai_enabled,
                    "risk_level": scenario.definition.risk_level,
                    "has_policy_comment": bool(policy_comment),
                },
            )
            if trace
            else None
        )
        generated = None
        provenance = None
        if self.ai_enabled and scenario.definition.risk_level <= 1 and policy_comment:
            generated, provenance = await self._generate_low_risk_response(
                response_context=response_ctx,
                service_id=task.get("ServiceId") or task.get("service_id"),
            )
        elif policy_comment:
            provenance = ResponseProvenance(
                source="template",
                tone="default",
                fallback_used=False,
                rag_candidate_count=len(kb_matches or []),
                rag_used_count=0,
            )

        if ai_span:
            if provenance and provenance.source != "template":
                ai_status = "fallback" if provenance.fallback_used else "succeeded"
                ai_span.finish(
                    status=ai_status,
                    output_data={
                        "source": provenance.source,
                        "backend": provenance.actual_backend,
                        "circuit": provenance.circuit,
                        "rag_used_count": provenance.rag_used_count,
                        "attempts": provenance.attempts,
                    },
                    metadata={
                        "requested_backend": provenance.requested_backend,
                        "fallback_reason_code": provenance.fallback_reason_code,
                        "rag_refs": provenance.rag_refs,
                    },
                    error_code=(
                        provenance.fallback_reason_code
                        if provenance.fallback_used
                        else None
                    ),
                    duration_ms=(
                        float(provenance.duration_ms)
                        if provenance.duration_ms is not None
                        else None
                    ),
                )
            elif not self.ai_enabled or scenario.definition.risk_level > 1 or not policy_comment:
                skip_reason = (
                    "ai_disabled"
                    if not self.ai_enabled
                    else "high_risk_scenario"
                    if scenario.definition.risk_level > 1
                    else "no_policy_comment"
                )
                ai_span.finish(
                    status="skipped",
                    metadata={"skip_reason": skip_reason},
                    duration_ms=None,
                )
            else:
                ai_span.finish(
                    status="succeeded",
                    output_data={"source": "template", "tone": "default"},
                )

        # 7. Guard step
        guard_span = (
            trace.start_span("guard", input_data={"generated_present": generated is not None})
            if trace
            else None
        )
        response = guarded_response(
            generated=generated,
            template_text=policy_comment,
            allowed_evidence_refs=candidate.evidence_refs,
            facts_summary=facts_summary,
            provenance=provenance,
        )
        if guard_span:
            guard_status = "fallback" if response.state == "fallback" else "succeeded"
            guard_span.finish(
                status=guard_status,
                output_data={
                    "state": response.state,
                    "violations_count": len(response.violations),
                    "replaced_by_template": response.state == "fallback" and generated is not None,
                },
                error_code=response.violations[0].code if response.violations else None,
            )

        internal_summary = build_internal_summary(
            scenario_key=scenario.definition.key,
            scenario_version=scenario.definition.version,
            facts_summary=facts_summary,
            diagnostics=diagnostics,
            plan=plan,
            task=task,
        )

        # 8. Compiler step
        compiler_span = (
            trace.start_span("compiler", input_data={"scenario_key": scenario.definition.key})
            if trace
            else None
        )
        compiled_envelope = await self.compiler.compile(
            scenario_key=scenario.definition.key,
            scenario_version=scenario.definition.version,
            scenario_risk=scenario.definition.risk_level,
            allowed_actions=set(scenario.definition.allowed_actions),
            facts=facts,
            candidates=[candidate],
            policy=policy,
            response=response,
            execution_plan=plan,
            internal_summary=internal_summary,
            diagnostics=diagnostics,
            decision_id=decision_id or str(uuid.uuid4()),
            decision_version=decision_version,
            service_id=task.get("ServiceId") or task.get("service_id"),
        )
        if compiler_span:
            gates = compiled_envelope.gates
            compiler_span.finish(
                status="succeeded",
                output_data={
                    "analysis_state": compiled_envelope.analysis_state,
                    "can_send_response": gates.can_send_response,
                    "can_execute_action": gates.can_execute_action,
                    "blocked_reasons": gates.blocked_reasons,
                },
            )

        return compiled_envelope
