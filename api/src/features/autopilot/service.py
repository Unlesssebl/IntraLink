"""Application service for ADR 0006 case/workflow/capability automation."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from time import perf_counter
import uuid
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.config import settings
from api.src.core.db import async_session_factory
from api.src.core.redis import get_redis_client
from api.src.core.task_dispatch import dispatch_command
from core.automation.capabilities import CapabilityRegistry, get_default_capability_registry
from core.automation.case_profiles import CaseProfileRegistry
from core.automation.case_router import CaseRouter
from core.automation.case_verifier import LLMCaseVerifier
from core.automation.clarification import (
    ONBOARDING_FACTS,
    ClarificationConvergenceService,
    ClarificationFactExtractor,
    FactCorrectionProposal,
    UniversalIntentExtractor,
    clarification_fingerprint,
    extract_labeled_facts,
    latest_human_public_event,
    merge_case_facts,
    merge_onboarding_facts,
    render_clarification,
)
from core.automation.compiler import WorkflowCompiler
from core.automation.contracts import (
    ActionPlan,
    ActionPlanState,
    ActionProposal,
    CaseCandidate,
    CaseDecision,
    CaseDecisionState,
    Disposition,
    ExtractionMethod,
    WorkflowPlan,
    WorkflowPlanState,
    compute_action_plan_hash,
)
from core.automation.frame_extractor import CaseFrameExtractor
from core.automation.intake import CaseIntakeEngine
from core.automation.llm_transport import LiteLLMVerifierTransport
from core.automation.persistence import AutomationBundle, AutomationRepository
from core.automation.policy import WorkflowPolicyService
from core.automation.preflight import ActionPreflightService
from core.automation.resolution import (
    HumanActionConflictError,
    HumanActionNotFoundError,
    HumanActionResolutionService,
    HumanActionValidationError,
)
from core.automation.runner import WorkflowRunner
from core.automation.service_catalog import ServiceCatalogRepository
from core.automation.service_routing import (
    LLMServiceTargetReranker,
    RedirectPlan,
    RedirectPlanState,
    RedirectResolver,
    ServiceCompatibilityDecision,
    ServiceCompatibilityState,
    ServiceTargetCandidate,
    TargetSelectionState,
    TargetServiceResolution,
    build_redirect_plan,
    compute_redirect_plan_hash,
)
from core.automation.snapshot import TicketSnapshotFactory
from core.automation.workflows import (
    WorkflowFactSpec,
    WorkflowRegistry,
    get_default_workflow_registry,
)
from core.database.models import (
    ActionPlanRecord,
    ActionPreflightRecord,
    CaseDecisionRecord,
    CaseFeedbackRecord,
    ClarificationRequestRecord,
    CommandRecord,
    ExecutionFeedbackRecord,
    HumanActionRequestRecord,
    OnboardingFactCorrectionRecord,
    PlanFeedbackRecord,
    RedirectFeedbackRecord,
    RedirectPlanRecord,
    ServiceRoutingFeedbackRecord,
    WorkflowPlanRecord,
)
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthError
from core.intraservice.client import IntraServiceClient

from .schemas import (
    ActionPlanFeedbackRequest,
    ApprovalResponse,
    ApproveActionPlanRequest,
    AutomationReadinessDTO,
    ClarificationReadinessDTO,
    CorrectActionPlanRequest,
    CorrectCaseDecisionRequest,
    CorrectFactsRequest,
    CorrectOnboardingFactsRequest,
    CorrectRedirectPlanRequest,
    CorrectTargetServiceRequest,
    FeedbackResponse,
    HumanActionCandidateDTO,
    HumanActionRequestDTO,
    PreflightReadinessDTO,
    ReadinessBlockerDTO,
    ReadinessStageDTO,
    RedirectPlanRequest,
    RedirectPlanResponse,
    ResolveHumanActionRequest,
    ResolveHumanActionResponse,
    RetryClarificationRequest,
    SuggestedCorrectionDTO,
    TicketAutomationDTO,
    WorkflowContextHeaderDTO,
)


class AutomationService:
    def __init__(
        self,
        *,
        client: IntraServiceClient | None = None,
        auth_bootstrap: ServiceAuthBootstrap | None = None,
        extractor: CaseFrameExtractor | None = None,
        router: CaseRouter | None = None,
        workflows: WorkflowRegistry | None = None,
        capabilities: CapabilityRegistry | None = None,
        repository: AutomationRepository | None = None,
        target_reranker: LLMServiceTargetReranker | None = None,
    ) -> None:
        self.client = client or IntraServiceClient(
            base_url=settings.INTRASERVICE_URL,
            verify_ssl=settings.SSL_VERIFY,
        )
        self.auth_bootstrap = auth_bootstrap or ServiceAuthBootstrap()
        profiles = CaseProfileRegistry()
        transport = LiteLLMVerifierTransport(
            base_url=settings.LITELLM_BASE_URL,
            api_key=settings.LITELLM_API_KEY,
        )
        self.extractor = extractor or CaseFrameExtractor(
            transport=transport,
            model_alias=settings.LITELLM_MODEL_FAST,
        )
        self.router = router or CaseRouter(
            profiles=profiles,
            verifier=LLMCaseVerifier(
                transport,
                profiles,
                model_alias=settings.LITELLM_MODEL_REASONING,
            ),
        )
        self.intake = CaseIntakeEngine(self.extractor, self.router)
        self.workflows = workflows or get_default_workflow_registry()
        self.capabilities = capabilities or get_default_capability_registry()
        self.compiler = WorkflowCompiler(self.workflows, self.capabilities)
        self.repository = repository or AutomationRepository()
        self.preflight = ActionPreflightService(self.capabilities)
        self.human_action_resolution = HumanActionResolutionService(
            self.capabilities,
            self.repository,
            self.preflight,
        )
        self.runner = WorkflowRunner(self.repository)
        self.workflow_policy = WorkflowPolicyService()
        self.catalog_repository = ServiceCatalogRepository()
        self.redirect_resolver = RedirectResolver()
        self.target_reranker = target_reranker or LLMServiceTargetReranker(
            transport,
            model_alias=settings.LITELLM_MODEL_FAST,
        )
        self.clarification_extractor = UniversalIntentExtractor(
            transport,
            model_alias=settings.LITELLM_MODEL_FAST,
        )
        self.clarification_convergence = ClarificationConvergenceService()

    async def _safe_bot_user_id(self) -> int | None:
        try:
            bot_credentials = await self.auth_bootstrap.bootstrap_auth(
                client=self.client,
                redis_client=get_redis_client(),
                session_factory=async_session_factory,
            )
            return bot_credentials.bot_user_id
        except Exception:
            return None

    async def _compute_suggested_corrections(
        self,
        snapshot,
        entities: dict[str, str],
        case_type: str | None,
        bot_user_id: int | None,
    ) -> list[SuggestedCorrectionDTO]:
        wf_def = self.workflows.for_case_type(case_type or "")
        if wf_def is None or not wf_def.facts or not snapshot.public_comments:
            return []
        proposals, _ = await self.clarification_extractor.detect_corrections_with_trace(
            snapshot.public_comments,
            entities,
            fact_specs=wf_def.facts,
            applicant_id=snapshot.applicant_id,
            bot_user_id=bot_user_id,
        )
        return [
            SuggestedCorrectionDTO(
                param_key=p.param_key,
                param_label=(
                    wf_def.fact_spec(p.param_key).label
                    if wf_def.fact_spec(p.param_key)
                    else p.param_key
                ),
                current_value=p.old_value,
                suggested_value=p.new_value,
                reason=p.reason,
                text_span=p.text_span,
                comment_id=p.comment_id,
            )
            for p in proposals
        ]

    async def get_automation(
        self, session: AsyncSession, ticket_id: int, auth_b64: str | None = None
    ) -> TicketAutomationDTO | None:
        bundle = await self.repository.latest_for_task(session, ticket_id)
        if bundle is None:
            return None
        diag = bundle.workflow_plan.fact_extraction_diagnostics or {}
        if "suggested_corrections" not in diag:
            try:
                return await self.analyze(session, ticket_id=ticket_id, auth_b64=auth_b64, force=False)
            except Exception:
                pass
        return await self._dto(session, bundle)

    async def analyze(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        auth_b64: str | None,
        force: bool,
    ) -> TicketAutomationDTO:
        analysis_started = perf_counter()
        token = await self._auth_token(auth_b64)
        fetch_started = perf_counter()
        task, lifetime = await asyncio.gather(
            self.client.get_task(task_id=ticket_id, auth_b64=token),
            self.client.get_task_lifetime(task_id=ticket_id, auth_b64=token),
        )
        timings = {"source_fetch": round((perf_counter() - fetch_started) * 1000)}
        snapshot = TicketSnapshotFactory.create(task, comments=lifetime)
        latest = await self.repository.latest_for_task(session, ticket_id)
        if not force and latest is not None and latest.frame.snapshot_hash == snapshot.snapshot_hash:
            bot_user_id = await self._safe_bot_user_id()
            suggested = await self._compute_suggested_corrections(
                snapshot=snapshot,
                entities=latest.frame.entities,
                case_type=latest.decision.primary_case_type,
                bot_user_id=bot_user_id,
            )
            existing_diag = latest.workflow_plan.fact_extraction_diagnostics or {}
            suggested_dicts = [c.model_dump(mode="json") for c in suggested]
            if existing_diag.get("suggested_corrections") != suggested_dicts:
                plan_rec = await session.get(WorkflowPlanRecord, latest.workflow_plan.id)
                if plan_rec is not None:
                    updated_plan = latest.workflow_plan.model_copy(
                        update={
                            "fact_extraction_diagnostics": {
                                **existing_diag,
                                "suggested_corrections": suggested_dicts,
                            }
                        }
                    )
                    plan_rec.plan_json = updated_plan.model_dump(mode="json")
                    await session.commit()
                    latest = AutomationBundle(
                        latest.frame,
                        latest.decision,
                        latest.compatibility,
                        updated_plan,
                        latest.action_plan,
                        latest.redirect_plan,
                    )
            return await self._dto(session, latest, suggested_corrections=suggested)

        catalog_started = perf_counter()
        version, entries, bindings = await self.catalog_repository.current(session)
        timings["catalog_load"] = round((perf_counter() - catalog_started) * 1000)
        target_started = perf_counter()
        target_resolution = self.redirect_resolver.resolve_target(
            snapshot=snapshot,
            catalog_hash=version.catalog_hash if version else None,
            entries=entries,
            catalog_fetched_at=version.fetched_at if version else None,
        )
        timings["target_retrieval"] = round((perf_counter() - target_started) * 1000)
        if target_resolution.state in {TargetSelectionState.ambiguous, TargetSelectionState.not_found}:
            rerank_started = perf_counter()
            target_resolution = await self.target_reranker.rerank(snapshot, target_resolution)
            timings["target_rerank"] = round((perf_counter() - rerank_started) * 1000)

        intake_started = perf_counter()
        frame, decision = await self.intake.analyze(
            snapshot,
            target_resolution=target_resolution,
        )
        extraction_diag: dict[str, Any] = {}
        suggested_corrections: list[SuggestedCorrectionDTO] = []
        bot_user_id = await self._safe_bot_user_id()
        bot_identity_error = None if bot_user_id is not None else "bot_identity_unavailable"

        wf_def = self.workflows.for_case_type(decision.primary_case_type or "")
        fact_specs: tuple[WorkflowFactSpec, ...] = ()
        allowed_wf_facts: set[str] = set()
        if wf_def is not None:
            fact_specs = wf_def.facts
            if not fact_specs and wf_def.required_facts:
                fact_specs = tuple(WorkflowFactSpec(key=k, label=k) for k in wf_def.required_facts)
            allowed_wf_facts = set(wf_def.required_facts) | {f.key for f in fact_specs}
            for cap_key in (*wf_def.diagnostic_capabilities, *wf_def.allowed_capabilities):
                spec = self.capabilities.get(cap_key)
                if spec is not None:
                    allowed_wf_facts.update(spec.required_params)
                    allowed_wf_facts.update(spec.optional_params)

        initial_facts: dict[str, Any] = {}
        if fact_specs:
            initial_facts, extraction_diag = await self._extract_workflow_snapshot_facts(
                snapshot,
                frame.entities,
                fact_specs=fact_specs,
                bot_user_id=bot_user_id,
                applicant_id=snapshot.applicant_id,
                bot_identity_error=bot_identity_error,
            )

        fact_merge = await self._merge_case_facts(
            session, frame.entities, ticket_id, initial_facts=initial_facts, allowed_keys=allowed_wf_facts or None
        )
        if fact_merge.values or fact_merge.conflicts or extraction_diag.get("llm_attempted"):
            frame = frame.model_copy(
                update={
                    "entities": {**frame.entities, **fact_merge.values},
                    "conflicting_facts": sorted(set(frame.conflicting_facts) | set(fact_merge.conflicts)),
                    "llm_attempted": frame.llm_attempted or bool(extraction_diag.get("llm_attempted")),
                }
            )
            decision = decision.model_copy(update={"frame_id": frame.id})

        if fact_specs:
            suggested_corrections = await self._compute_suggested_corrections(
                snapshot=snapshot,
                entities=frame.entities,
                case_type=decision.primary_case_type,
                bot_user_id=bot_user_id,
            )
        timings["case_classification"] = round((perf_counter() - intake_started) * 1000)
        compatibility, binding = self._compatibility_from_catalog(
            snapshot=snapshot,
            decision=decision,
            catalog_hash=version.catalog_hash if version else None,
            entries=entries,
            bindings=bindings,
            target_resolution=target_resolution,
        )
        llm_used = (
            target_resolution.llm_used
            or frame.llm_attempted
            or bool(extraction_diag.get("llm_used"))
            or any(item.extraction_method == ExtractionMethod.llm for item in frame.assertions)
            or bool(decision.verifier_trace)
        )
        compatibility = compatibility.model_copy(
            update={
                "analysis_timings_ms": timings,
                "llm_used": llm_used,
            }
        )
        clarification_round = await self._published_clarification_count(session, ticket_id)
        workflow, action_plan = self._compile(frame, decision, compatibility, binding, clarification_round)
        extraction_diag_with_suggestions = {
            **extraction_diag,
            "suggested_corrections": [c.model_dump(mode="json") for c in suggested_corrections],
        }
        workflow = workflow.model_copy(
            update={
                "fact_provenance": fact_merge.provenance,
                "fact_conflicts": fact_merge.conflicts,
                "fact_extraction_diagnostics": extraction_diag_with_suggestions,
            }
        )
        if action_plan is not None:
            action_plan = action_plan.model_copy(update={"workflow_plan_id": workflow.id})
            action_plan = action_plan.model_copy(update={"plan_hash": compute_action_plan_hash(action_plan)})
        timings["total_before_persistence"] = round((perf_counter() - analysis_started) * 1000)
        compatibility = compatibility.model_copy(update={"analysis_timings_ms": timings})
        redirect_plan = (
            build_redirect_plan(compatibility, decision)
            if compatibility.state == ServiceCompatibilityState.mismatch
            else None
        )
        bundle = await self.repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            compatibility=compatibility,
            workflow_plan=workflow,
            action_plan=action_plan,
            redirect_plan=redirect_plan,
        )
        if action_plan is not None:
            await self.preflight.run_plan(session, action_plan_id=action_plan.id)
        await session.commit()
        if (
            workflow.workflow_key == "employee_onboarding_workflow"
            and workflow.state == WorkflowPlanState.awaiting_facts
        ):
            bundle = await self._ensure_clarification(
                session,
                bundle=bundle,
                lifetime=lifetime,
                auth_b64=token,
            )
        elif workflow.workflow_key == "employee_onboarding_workflow":
            await self.clarification_convergence.converge(
                session,
                task_id=workflow.task_id,
                missing_facts=workflow.missing_facts,
                snapshot_hash=workflow.snapshot_hash,
            )
            await session.commit()
        return await self._dto(session, bundle, suggested_corrections=suggested_corrections)


    async def resume(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        event_id: int,
    ) -> TicketAutomationDTO:
        """Resolve one published clarification from a new public human response."""
        credentials = await self.auth_bootstrap.bootstrap_auth(
            client=self.client,
            redis_client=get_redis_client(),
            session_factory=async_session_factory,
        )
        record = await session.scalar(
            select(ClarificationRequestRecord)
            .where(
                ClarificationRequestRecord.task_id == ticket_id,
                ClarificationRequestRecord.state == "published",
            )
            .order_by(ClarificationRequestRecord.published_at.desc())
            .with_for_update()
            .limit(1)
        )
        if record is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "clarification_not_awaiting")
        task, lifetime = await asyncio.gather(
            self.client.get_task(task_id=ticket_id, auth_b64=credentials.auth_b64),
            self.client.get_task_lifetime(task_id=ticket_id, auth_b64=credentials.auth_b64),
        )
        event = latest_human_public_event(
            lifetime,
            baseline_event_id=record.baseline_event_id,
            baseline_event_key=record.baseline_event_key,
            bot_user_id=credentials.bot_user_id,
            applicant_id=task.applicant_id,
            question_text=record.question_text,
        )
        if event is None or event.id != event_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "clarification_event_not_eligible")
        try:
            facts = await self.clarification_extractor.extract(event.comment or "", record.missing_facts_json)
        except Exception:
            # Deterministic labelled values remain usable when the optional LLM is unavailable.
            facts = extract_labeled_facts(event.comment or "", record.missing_facts_json)
        if not facts:
            record.last_error_code = f"no_facts_in_event:{event_id}"
            record.last_error_detail = f"Ответ пользователя (событие {event_id}) не содержит обязательных фактов."
            record.last_attempt_at = datetime.now(UTC)
            await session.commit()
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "clarification_response_empty")
        record.response_event_id = event_id
        record.response_event_key = event.event_key
        record.response_facts_json = facts
        record.resolved_at = datetime.now(UTC)
        record.state = "resolved"
        record.last_error_code = None
        record.last_error_detail = None
        await session.commit()
        return await self.analyze(session, ticket_id=ticket_id, auth_b64=credentials.auth_b64, force=True)

    async def correct_facts(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: CorrectFactsRequest,
        operator: str,
        auth_b64: str | None,
    ) -> TicketAutomationDTO:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        latest = await self.repository.latest_for_task(session, ticket_id)
        if latest is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket_automation_not_found")
        cleaned = {key: " ".join(value.split()) for key, value in request.facts.items()}
        if any(not value for value in cleaned.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_fact_correction_empty_value")
        for key, value in cleaned.items():
            session.add(
                OnboardingFactCorrectionRecord(
                    task_id=ticket_id,
                    snapshot_hash=request.snapshot_hash,
                    fact_key=key,
                    value=value,
                    operator_username=operator,
                    reason_tag=request.reason_tag,
                )
            )
        await session.commit()
        return await self.analyze(session, ticket_id=ticket_id, auth_b64=auth_b64, force=True)

    async def correct_onboarding_facts(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: CorrectOnboardingFactsRequest,
        operator: str,
        auth_b64: str | None,
    ) -> TicketAutomationDTO:
        latest = await self.repository.latest_for_task(session, ticket_id)
        if latest is not None and latest.workflow_plan.workflow_key == "employee_onboarding_workflow":
            invalid = sorted(set(request.facts) - set(ONBOARDING_FACTS))
            if invalid:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_onboarding_fact_correction")
        return await self.correct_facts(
            session,
            ticket_id=ticket_id,
            request=request,
            operator=operator,
            auth_b64=auth_b64,
        )

    async def _extract_workflow_snapshot_facts(
        self,
        snapshot,
        existing: dict[str, str],
        *,
        fact_specs: Iterable[WorkflowFactSpec],
        bot_user_id: int | None,
        applicant_id: int | None,
        bot_identity_error: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        specs_list = list(fact_specs)
        missing = [key for key in (s.key for s in specs_list) if not str(existing.get(key, "")).strip()]
        diag: dict[str, Any] = {"llm_attempted": False, "llm_used": False, "trace": []}
        if bot_identity_error:
            diag["trace"].append({"stage": "bot_identity", "error": bot_identity_error})
        if applicant_id is None:
            diag["trace"].append({"stage": "applicant_identity", "error": "applicant_id_unavailable"})
        if not missing:
            return {}, diag

        extracted_facts: dict[str, Any] = {}

        # 1. Deterministic extraction from ticket title & description
        ticket_body = f"{snapshot.title}\n{snapshot.description}".strip()
        if ticket_body:
            body_facts = extract_labeled_facts(ticket_body, missing, fact_specs=specs_list)
            for k, v in body_facts.items():
                extracted_facts[k] = v
            missing = [k for k in missing if k not in extracted_facts]

        # 2. Deterministic extraction from individual public human comments (chronological order)
        bot_markers = (
            "Укажите, пожалуйста, полные ФИО",
            "Для создания учётной записи дополнительно укажите",
        )
        for comment in snapshot.public_comments:
            if not missing:
                break
            text = (comment.text or "").strip()
            if not text:
                continue
            if bot_user_id is None:
                continue
            if comment.editor_id == bot_user_id:
                continue
            if applicant_id is None or comment.editor_id != applicant_id:
                continue
            if any(marker in text for marker in bot_markers):
                continue
            comment_facts = extract_labeled_facts(text, missing, fact_specs=specs_list)
            for k, v in comment_facts.items():
                if isinstance(v, dict):
                    v = {**v, "comment_id": comment.id}
                extracted_facts[k] = v
            missing = [k for k in missing if k not in extracted_facts]

        # 3. If missing facts remain, attempt bounded LLM fallback on human comments & body
        if missing:
            candidates_for_llm: list[str] = []
            for comment in reversed(snapshot.public_comments):
                text = (comment.text or "").strip()
                if not text or bot_user_id is None:
                    continue
                if comment.editor_id == bot_user_id:
                    continue
                if applicant_id is None or comment.editor_id != applicant_id:
                    continue
                if any(marker in text for marker in bot_markers):
                    continue
                candidates_for_llm.append(text)
            if ticket_body and len(ticket_body) > 10:
                candidates_for_llm.append(ticket_body)

            for cand_text in candidates_for_llm:
                if not missing:
                    break
                try:
                    llm_facts, llm_diag = await self.clarification_extractor.extract_with_trace(
                        cand_text[:4000], missing, fact_specs=specs_list
                    )
                    diag["trace"].append(llm_diag)
                    if llm_diag.get("llm_attempted"):
                        diag["llm_attempted"] = True
                    if llm_diag.get("llm_used"):
                        diag["llm_used"] = True
                    for k, v in llm_facts.items():
                        extracted_facts[k] = v
                    missing = [k for k in missing if k not in extracted_facts]
                except Exception as exc:
                    diag["trace"].append({"error": str(exc)})

        return extracted_facts, diag

    async def _extract_onboarding_snapshot_facts(
        self,
        snapshot,
        existing: dict[str, str],
        *,
        bot_user_id: int | None,
        applicant_id: int | None,
        bot_identity_error: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        wf_def = self.workflows.for_case_type("employee_onboarding")
        specs = wf_def.facts if wf_def else ()
        return await self._extract_workflow_snapshot_facts(
            snapshot,
            existing,
            fact_specs=specs,
            bot_user_id=bot_user_id,
            applicant_id=applicant_id,
            bot_identity_error=bot_identity_error,
        )


    async def _merge_case_facts(
        self,
        session: AsyncSession,
        structured: dict[str, str],
        task_id: int,
        *,
        initial_facts: dict[str, Any] | None = None,
        allowed_keys: Iterable[str] | None = None,
    ):
        records = list(
            (
                await session.scalars(
                    select(ClarificationRequestRecord)
                    .where(
                        ClarificationRequestRecord.task_id == task_id,
                        ClarificationRequestRecord.state == "resolved",
                    )
                    .order_by(ClarificationRequestRecord.round.asc())
                )
            ).all()
        )
        responses = []
        if initial_facts:
            responses.append({"source": "ticket_text", "event_id": None, "facts": initial_facts})
        responses.extend(
            [
                {
                    "source": "public_comment",
                    "event_id": item.response_event_id,
                    "facts": item.response_facts_json,
                }
                for item in records
            ]
        )
        corrections = list(
            (
                await session.scalars(
                    select(OnboardingFactCorrectionRecord)
                    .where(OnboardingFactCorrectionRecord.task_id == task_id)
                    .order_by(OnboardingFactCorrectionRecord.created_at.asc())
                )
            ).all()
        )
        responses.extend(
            {
                "source": "operator_correction",
                "event_id": None,
                "facts": {item.fact_key: {"value": item.value, "text_span": None}},
            }
            for item in corrections
        )
        return merge_case_facts(structured, responses, allowed_keys=allowed_keys)

    async def _merge_onboarding_facts(
        self,
        session: AsyncSession,
        structured: dict[str, str],
        task_id: int,
        *,
        initial_facts: dict[str, Any] | None = None,
    ):
        return await self._merge_case_facts(
            session, structured, task_id, initial_facts=initial_facts, allowed_keys=ONBOARDING_FACTS
        )

    @staticmethod
    async def _published_clarification_count(session: AsyncSession, task_id: int) -> int:
        from sqlalchemy import func

        return int(
            await session.scalar(
                select(func.count())
                .select_from(ClarificationRequestRecord)
                .where(
                    ClarificationRequestRecord.task_id == task_id,
                    ClarificationRequestRecord.state.in_(["published", "resolved"]),
                )
            )
            or 0
        )

    async def _ensure_clarification(
        self,
        session: AsyncSession,
        *,
        bundle: AutomationBundle,
        lifetime,
        auth_b64: str,
    ) -> AutomationBundle:
        workflow = bundle.workflow_plan
        active = await session.scalar(
            select(ClarificationRequestRecord)
            .where(
                ClarificationRequestRecord.task_id == workflow.task_id,
                ClarificationRequestRecord.state.in_(["pending_publish", "published"]),
            )
            .order_by(ClarificationRequestRecord.created_at.desc())
            .limit(1)
        )
        if active is None:
            question = render_clarification(workflow.missing_facts)
            fingerprint = clarification_fingerprint(workflow.task_id, workflow.snapshot_hash, workflow.missing_facts)
            active = await session.scalar(
                select(ClarificationRequestRecord).where(ClarificationRequestRecord.request_fingerprint == fingerprint)
            )
            if active is None:
                active = ClarificationRequestRecord(
                    task_id=workflow.task_id,
                    workflow_plan_id=workflow.id,
                    workflow_key=workflow.workflow_key,
                    round=workflow.clarification_round + 1,
                    snapshot_hash=workflow.snapshot_hash,
                    missing_facts_json=workflow.missing_facts,
                    request_fingerprint=fingerprint,
                    baseline_event_id=lifetime[-1].id if lifetime else None,
                    baseline_event_key=lifetime[-1].event_key if lifetime else None,
                    question_text=question,
                    state="pending_publish",
                )
                session.add(active)
                await session.commit()

        restore_waiting = active.state == "pending_publish" and workflow.state == WorkflowPlanState.needs_review
        updated = workflow.model_copy(
            update={
                "active_clarification_id": active.id,
                **(
                    {
                        "state": WorkflowPlanState.awaiting_facts,
                        "disposition": Disposition.clarify,
                        "missing_facts": list(active.missing_facts_json or []),
                        "reason_codes": [
                            code
                            for code in workflow.reason_codes
                            if not code.startswith("clarification_publish_failed:")
                        ],
                    }
                    if restore_waiting
                    else {}
                ),
            }
        )
        plan_record = await session.get(WorkflowPlanRecord, workflow.id)
        if plan_record is not None:
            plan_record.state = updated.state.value
            plan_record.disposition = updated.disposition.value
            plan_record.plan_json = updated.model_dump(mode="json")

        if active.state == "failed":
            failed = updated.model_copy(
                update={
                    "state": WorkflowPlanState.needs_review,
                    "disposition": Disposition.manual,
                    "missing_facts": [],
                    "reason_codes": [
                        *[
                            code
                            for code in updated.reason_codes
                            if not code.startswith("clarification_publish_failed:")
                        ],
                        f"clarification_publish_failed:{active.last_error_code or 'unknown'}",
                    ],
                }
            )
            if plan_record is not None:
                plan_record.state = failed.state.value
                plan_record.disposition = failed.disposition.value
                plan_record.plan_json = failed.model_dump(mode="json")
            await session.commit()
            return AutomationBundle(
                bundle.frame,
                bundle.decision,
                bundle.compatibility,
                failed,
                None,
                bundle.redirect_plan,
            )

        if active.state == "pending_publish":
            credentials = await self.auth_bootstrap.bootstrap_auth(
                client=self.client,
                redis_client=get_redis_client(),
                session_factory=async_session_factory,
            )
            boundary_index: int | None = None
            if active.baseline_event_key:
                boundary_indexes = [
                    index for index, item in enumerate(lifetime) if item.event_key == active.baseline_event_key
                ]
                if boundary_indexes:
                    boundary_index = boundary_indexes[-1]
            elif active.baseline_event_id is not None:
                boundary_indexes = [index for index, item in enumerate(lifetime) if item.id == active.baseline_event_id]
                if boundary_indexes:
                    boundary_index = boundary_indexes[-1]
            candidate_events = lifetime[(boundary_index + 1) :] if boundary_index is not None else lifetime
            already_published = any(
                item.editor_id == credentials.bot_user_id
                and not item.is_private
                and (item.comment or "").strip() == active.question_text
                for item in candidate_events
            )
            try:
                active.publish_attempts = int(active.publish_attempts or 0) + 1
                active.last_attempt_at = datetime.now(UTC)
                if not already_published:
                    bot_token = credentials.auth_b64 or auth_b64
                    await self.client.update_task(
                        task_id=workflow.task_id,
                        comment=active.question_text,
                        is_private=False,
                        auth_b64=bot_token,
                    )
                active.state = "published"
                active.published_at = datetime.now(UTC)
                active.last_error_code = None
                active.last_error_detail = None
            except Exception as exc:
                failed = updated.model_copy(
                    update={
                        "state": WorkflowPlanState.needs_review,
                        "disposition": Disposition.manual,
                        "missing_facts": [],
                        "reason_codes": [*updated.reason_codes, f"clarification_publish_failed:{type(exc).__name__}"],
                    }
                )
                active.state = "failed"
                active.last_error_code = type(exc).__name__[:128]
                active.last_error_detail = f"Не удалось опубликовать вопрос ({type(exc).__name__})."[:500]
                if plan_record is not None:
                    plan_record.state = failed.state.value
                    plan_record.disposition = failed.disposition.value
                    plan_record.plan_json = failed.model_dump(mode="json")
                await session.commit()
                return AutomationBundle(
                    bundle.frame, bundle.decision, bundle.compatibility, failed, None, bundle.redirect_plan
                )
        await session.commit()
        return AutomationBundle(
            bundle.frame, bundle.decision, bundle.compatibility, updated, bundle.action_plan, bundle.redirect_plan
        )

    async def retry_clarification(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        clarification_id,
        request: RetryClarificationRequest,
        auth_b64: str | None,
    ) -> TicketAutomationDTO:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        bundle = await self.repository.latest_for_task(session, ticket_id)
        if bundle is None or bundle.workflow_plan.workflow_key != "employee_onboarding_workflow":
            raise HTTPException(status.HTTP_409_CONFLICT, "employee_onboarding_not_active")
        record = await session.scalar(
            select(ClarificationRequestRecord)
            .where(
                ClarificationRequestRecord.id == clarification_id,
                ClarificationRequestRecord.task_id == ticket_id,
            )
            .with_for_update()
        )
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "clarification_not_found")
        if record.snapshot_hash != request.snapshot_hash or record.id != bundle.workflow_plan.active_clarification_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "clarification_snapshot_changed")
        if record.state not in {"failed", "pending_publish"}:
            raise HTTPException(status.HTTP_409_CONFLICT, "clarification_not_retryable")
        record.state = "pending_publish"
        await session.commit()
        token = await self._auth_token(auth_b64)
        lifetime = await self.client.get_task_lifetime(task_id=ticket_id, auth_b64=token)
        bundle = await self._ensure_clarification(session, bundle=bundle, lifetime=lifetime, auth_b64=token)
        return await self._dto(session, bundle)

    async def approve(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: ApproveActionPlanRequest,
        operator: str,
        auth_b64: str | None,
    ) -> ApprovalResponse:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        plan_record = await session.scalar(
            select(ActionPlanRecord).where(ActionPlanRecord.id == request.action_plan_id)
        )
        if plan_record is None or plan_record.task_id != ticket_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "action_plan_not_found")
        try:
            plan_for_policy = ActionPlan.model_validate(plan_record.plan_json)
            await self.workflow_policy.require_assisted(session, workflow_key=plan_for_policy.workflow_key)
            if (
                any(item.capability_key == "create_ad_user" for item in plan_for_policy.actions)
                and not settings.AD_ONBOARDING_EXECUTION_ENABLED
            ):
                raise ValueError("ad_onboarding_execution_disabled")
            if any(item.capability_key == "create_ad_user" for item in plan_for_policy.actions):
                version, _entries, bindings = await self.catalog_repository.current(session)
                binding = next(
                    (
                        item
                        for item in bindings
                        if item.key == plan_for_policy.service_binding_key
                        and item.version == plan_for_policy.service_binding_version
                    ),
                    None,
                )
                if (
                    version is None
                    or version.catalog_hash != plan_for_policy.catalog_hash
                    or binding is None
                    or not binding.is_active
                    or not binding.is_validated
                ):
                    raise ValueError("ad_service_binding_changed")
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        try:
            plan = await self.repository.approve_action_plan(
                session,
                action_plan_id=request.action_plan_id,
                expected_plan_hash=request.plan_hash,
                expected_snapshot_hash=request.snapshot_hash,
                operator_username=operator,
            )
        except (LookupError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        feedback = PlanFeedbackRecord(
            action_plan_id=plan.id,
            task_id=ticket_id,
            operator_username=operator,
            verdict="approved",
            original_plan_hash=plan.plan_hash,
        )
        session.add(feedback)
        advanced = await self.runner.advance(session, action_plan_id=plan.id, initiator=operator)
        await session.commit()
        command_id = advanced.command.id if advanced.command is not None else None
        if command_id is not None:
            await dispatch_command(command_id)
        return ApprovalResponse(
            status="approved",
            action_plan_id=plan.id,
            command_id=command_id,
            plan_hash=plan.plan_hash,
        )

    async def record_plan_feedback(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: ActionPlanFeedbackRequest,
        operator: str,
        verdict: str,
        auth_b64: str | None,
    ) -> FeedbackResponse:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        record = await session.scalar(
            select(ActionPlanRecord).where(ActionPlanRecord.id == request.action_plan_id).with_for_update()
        )
        if record is None or record.task_id != ticket_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "action_plan_not_found")
        if record.plan_hash != request.plan_hash or record.snapshot_hash != request.snapshot_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_or_tampered_action_plan")
        if record.state != ActionPlanState.ready.value:
            raise HTTPException(status.HTTP_409_CONFLICT, "action_plan_not_ready")
        if await session.scalar(select(PlanFeedbackRecord.id).where(PlanFeedbackRecord.action_plan_id == record.id)):
            raise HTTPException(status.HTTP_409_CONFLICT, "action_plan_already_reviewed")
        feedback = PlanFeedbackRecord(
            action_plan_id=record.id,
            task_id=ticket_id,
            operator_username=operator,
            verdict=verdict,
            original_plan_hash=record.plan_hash,
            reason_tag=request.reason_tag,
            notes=request.notes,
        )
        record.state = "cancelled"
        session.add(feedback)
        await session.commit()
        return FeedbackResponse(status=verdict, feedback_id=feedback.id, action_plan_id=record.id)

    async def correct_action_plan(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: CorrectActionPlanRequest,
        operator: str,
        auth_b64: str | None,
    ) -> TicketAutomationDTO:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        record = await session.scalar(
            select(ActionPlanRecord).where(ActionPlanRecord.id == request.action_plan_id).with_for_update()
        )
        if record is None or record.task_id != ticket_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "action_plan_not_found")
        if record.plan_hash != request.plan_hash or record.snapshot_hash != request.snapshot_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_or_tampered_action_plan")
        if record.state != ActionPlanState.ready.value:
            raise HTTPException(status.HTTP_409_CONFLICT, "action_plan_not_ready")
        if await session.scalar(select(PlanFeedbackRecord.id).where(PlanFeedbackRecord.action_plan_id == record.id)):
            raise HTTPException(status.HTTP_409_CONFLICT, "action_plan_already_reviewed")

        original = ActionPlan.model_validate(record.plan_json)
        workflow = self.workflows.get(original.workflow_key)
        if workflow is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "workflow_not_registered")
        allowed = set(workflow.allowed_capabilities)
        proposals: list[ActionProposal] = []
        previous_id: str | None = None
        for sequence_no, corrected in enumerate(request.actions):
            if corrected.capability_key not in allowed:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "capability_not_allowed_by_workflow")
            spec = self.capabilities.require(corrected.capability_key)
            if not spec.enabled:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "capability_disabled")
            accepted_params = set((*spec.required_params, *spec.optional_params))
            if set(corrected.params) - accepted_params:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_capability_params")
            if self.capabilities.validate_params(corrected.capability_key, corrected.params):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "required_capability_params_missing")
            action_id = f"operator_{sequence_no}_{corrected.capability_key}"
            proposals.append(
                ActionProposal(
                    id=action_id,
                    capability_key=corrected.capability_key,
                    sequence_no=sequence_no,
                    params=corrected.params,
                    depends_on_action_ids=[previous_id] if previous_id else [],
                    risk=spec.risk.value,
                    requires_approval=True,
                )
            )
            previous_id = action_id

        draft = ActionPlan(
            task_id=original.task_id,
            snapshot_hash=original.snapshot_hash,
            case_decision_id=original.case_decision_id,
            workflow_plan_id=original.workflow_plan_id,
            workflow_key=original.workflow_key,
            workflow_version=original.workflow_version,
            source_service_id=original.source_service_id,
            service_binding_key=original.service_binding_key,
            service_binding_version=original.service_binding_version,
            catalog_hash=original.catalog_hash,
            state=ActionPlanState.ready,
            disposition=Disposition.execute,
            actions=proposals,
        )
        corrected_plan = draft.model_copy(update={"plan_hash": compute_action_plan_hash(draft)})
        try:
            await self.repository.save_action_plan(session, corrected_plan)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        feedback = PlanFeedbackRecord(
            action_plan_id=record.id,
            task_id=ticket_id,
            operator_username=operator,
            verdict="corrected",
            original_plan_hash=record.plan_hash,
            corrected_action_plan_id=corrected_plan.id,
            reason_tag=request.reason_tag,
            notes=request.notes,
        )
        record.state = ActionPlanState.cancelled.value
        session.add(feedback)
        await self.preflight.run_plan(session, action_plan_id=corrected_plan.id)
        await session.commit()
        bundle = await self.repository.latest_for_task(session, ticket_id)
        if bundle is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "automation_bundle_missing")
        return await self._dto(session, bundle)

    async def correct_case(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: CorrectCaseDecisionRequest,
        operator: str,
        auth_b64: str | None,
    ) -> TicketAutomationDTO:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        original = await session.scalar(
            select(CaseDecisionRecord).where(CaseDecisionRecord.id == request.case_decision_id)
        )
        if original is None or original.task_id != ticket_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "case_decision_not_found")
        if original.snapshot_hash != request.snapshot_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_ticket_snapshot")
        profile = self.router.profiles.get(request.corrected_case_type)
        if profile is None and request.corrected_case_type != "unknown":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_case_type")
        previous_plans = list(
            (
                await session.scalars(
                    select(ActionPlanRecord).where(ActionPlanRecord.case_decision_id == original.id).with_for_update()
                )
            ).all()
        )
        if any(
            item.state in {ActionPlanState.approved.value, ActionPlanState.running.value} for item in previous_plans
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "case_correction_after_approval_forbidden")
        frame = self._operator_frame(original.case_frame_json)
        if profile is None:
            decision = CaseDecision(
                task_id=ticket_id,
                snapshot_hash=request.snapshot_hash,
                frame_id=frame.id,
                router_version="operator-correction-v1",
                state=CaseDecisionState.unknown,
                reason_codes=["operator_marked_unknown"],
            )
        else:
            decision = CaseDecision(
                task_id=ticket_id,
                snapshot_hash=request.snapshot_hash,
                frame_id=frame.id,
                router_version="operator-correction-v1",
                state=CaseDecisionState.selected,
                primary_case_type=profile.case_type,
                candidates=[CaseCandidate(case_type=profile.case_type, case_type_version=profile.version)],
                reason_codes=["operator_case_correction"],
            )
        latest = await self.repository.latest_for_task(session, ticket_id)
        if latest is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "automation_bundle_missing")
        version, entries, bindings = await self.catalog_repository.current(session)
        compatibility = self.redirect_resolver.resolve(
            decision=decision,
            source_service_id=latest.compatibility.source_service_id,
            source_task_type_id=latest.compatibility.source_task_type_id,
            catalog_hash=version.catalog_hash if version else None,
            entries=entries,
            bindings=bindings,
        )
        binding = next(
            (
                item
                for item in bindings
                if item.key == compatibility.binding_key and item.version == compatibility.binding_version
            ),
            None,
        )
        workflow, action_plan = self._compile(frame, decision, compatibility, binding)
        redirect_plan = (
            build_redirect_plan(compatibility, decision)
            if compatibility.state == ServiceCompatibilityState.mismatch
            else None
        )
        bundle = await self.repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            compatibility=compatibility,
            workflow_plan=workflow,
            action_plan=action_plan,
            redirect_plan=redirect_plan,
        )
        session.add(
            CaseFeedbackRecord(
                case_decision_id=original.id,
                task_id=ticket_id,
                snapshot_hash=request.snapshot_hash,
                operator_username=operator,
                verdict="corrected",
                original_case_type=original.primary_case_type,
                corrected_case_type=request.corrected_case_type,
                reason_tag=request.reason_tag,
                notes=request.notes,
            )
        )
        for previous in previous_plans:
            previous.state = ActionPlanState.cancelled.value
            if not await session.scalar(
                select(PlanFeedbackRecord.id).where(PlanFeedbackRecord.action_plan_id == previous.id)
            ):
                session.add(
                    PlanFeedbackRecord(
                        action_plan_id=previous.id,
                        task_id=ticket_id,
                        operator_username=operator,
                        verdict="corrected",
                        original_plan_hash=previous.plan_hash,
                        corrected_action_plan_id=action_plan.id if action_plan is not None else None,
                        reason_tag="case_decision_corrected",
                    )
                )
        if action_plan is not None:
            await self.preflight.run_plan(session, action_plan_id=action_plan.id)
        await session.commit()
        return await self._dto(session, bundle)

    async def correct_target_service(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: CorrectTargetServiceRequest,
        operator: str,
        auth_b64: str | None,
    ) -> TicketAutomationDTO:
        token = await self._auth_token(auth_b64)
        task, lifetime = await asyncio.gather(
            self.client.get_task(task_id=ticket_id, auth_b64=token),
            self.client.get_task_lifetime(task_id=ticket_id, auth_b64=token),
        )
        snapshot = TicketSnapshotFactory.create(task, comments=lifetime)
        if snapshot.snapshot_hash != request.snapshot_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_ticket_snapshot")

        latest = await self.repository.latest_for_task(session, ticket_id)
        if latest is None or latest.compatibility.id != request.compatibility_decision_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_service_decision")
        version, entries, bindings = await self.catalog_repository.current(session)
        if version is None or latest.compatibility.catalog_hash != version.catalog_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "service_catalog_changed")
        active_entries = {
            item.service_id: item for item in entries if item.is_active and item.catalog_hash == version.catalog_hash
        }
        target = active_entries.get(request.target_service_id)
        if target is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "target_service_unavailable")
        if any(item.parent_service_id == target.service_id for item in active_entries.values()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "target_service_must_be_leaf")

        source = active_entries.get(snapshot.service_id) if snapshot.service_id is not None else None
        target_state = (
            TargetSelectionState.source_match
            if snapshot.service_id == target.service_id
            else TargetSelectionState.target_suggested
        )
        resolution = TargetServiceResolution(
            task_id=ticket_id,
            snapshot_hash=snapshot.snapshot_hash,
            source_service_id=snapshot.service_id,
            source_service_path=source.service_path if source else None,
            catalog_hash=version.catalog_hash,
            catalog_state="available",
            state=target_state,
            selected_service_id=target.service_id,
            selected_service_path=target.service_path,
            method="operator_confirmation",
            candidates=[
                ServiceTargetCandidate(
                    service_id=target.service_id,
                    service_path=target.service_path,
                    confidence="high",
                    evidence=["operator_confirmed"],
                )
            ],
            evidence=["operator_confirmed"],
            reason_codes=["operator_target_correction"],
        )
        started = perf_counter()
        frame, decision = await self.intake.analyze(snapshot, target_resolution=resolution)
        compatibility, binding = self._compatibility_from_catalog(
            snapshot=snapshot,
            decision=decision,
            catalog_hash=version.catalog_hash,
            entries=entries,
            bindings=bindings,
            target_resolution=resolution,
        )
        compatibility = compatibility.model_copy(
            update={
                "analysis_timings_ms": {"operator_reclassification": round((perf_counter() - started) * 1000)},
                "llm_used": frame.llm_attempted or bool(decision.verifier_trace),
            }
        )
        workflow, action_plan = self._compile(frame, decision, compatibility, binding)
        redirect_plan = (
            build_redirect_plan(compatibility, decision)
            if compatibility.state == ServiceCompatibilityState.mismatch
            else None
        )
        bundle = await self.repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            compatibility=compatibility,
            workflow_plan=workflow,
            action_plan=action_plan,
            redirect_plan=redirect_plan,
        )
        session.add(
            ServiceRoutingFeedbackRecord(
                compatibility_decision_id=latest.compatibility.id,
                task_id=ticket_id,
                operator_username=operator,
                verdict="corrected" if target_state == TargetSelectionState.target_suggested else "confirmed",
                selected_target_service_id=target.service_id,
                reason_tag=request.reason_tag,
                notes=request.notes,
            )
        )
        if action_plan is not None:
            await self.preflight.run_plan(session, action_plan_id=action_plan.id)
        await session.commit()
        return await self._dto(session, bundle)

    async def approve_redirect(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: RedirectPlanRequest,
        operator: str,
        auth_b64: str | None,
    ) -> RedirectPlanResponse:
        token = await self._auth_token(auth_b64)
        task = await self.client.get_task(task_id=ticket_id, auth_b64=token)
        lifetime = await self.client.get_task_lifetime(task_id=ticket_id, auth_b64=token)
        current = TicketSnapshotFactory.create(task, comments=lifetime)
        record, plan = await self._locked_redirect(session, ticket_id, request)
        if current.snapshot_hash != plan.snapshot_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_ticket_snapshot")
        if current.service_id != plan.source_service_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "source_service_changed")
        version, entries, bindings = await self.catalog_repository.current(session)
        if version is None or version.catalog_hash != plan.catalog_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "service_catalog_changed")
        target = next((item for item in entries if item.service_id == plan.target_service_id and item.is_active), None)
        binding = next(
            (item for item in bindings if item.key == plan.binding_key and item.version == plan.binding_version), None
        )
        if target is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "redirect_target_unavailable")
        if (
            binding is None
            or not binding.is_active
            or not binding.is_validated
            or target.service_id not in binding.service_ids
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "service_binding_changed")
        if plan.strategy.value != "cancel_and_recreate":
            raise HTTPException(status.HTTP_409_CONFLICT, "redirect_strategy_unsupported")

        approved = plan.model_copy(update={"state": RedirectPlanState.approved, "approval_state": "approved"})
        record.state = approved.state.value
        record.approval_state = approved.approval_state
        record.approved_by = operator
        record.approved_at = datetime.now(UTC)
        record.plan_json = approved.model_dump(mode="json")
        session.add(
            RedirectFeedbackRecord(
                redirect_plan_id=record.id,
                task_id=ticket_id,
                operator_username=operator,
                verdict="approved",
                selected_target_service_id=plan.target_service_id,
            )
        )
        await session.commit()

        steps = list(approved.execution_steps)
        try:
            await self.client.add_task_comment(
                task_id=ticket_id,
                comment=approved.rendered_public_comment,
                is_private=False,
                auth_b64=token,
            )
            steps.append({"step": "public_comment", "state": "confirmed", "at": datetime.now(UTC).isoformat()})
        except Exception as exc:
            failed = approved.model_copy(
                update={
                    "state": RedirectPlanState.needs_review,
                    "execution_state": "comment_unknown",
                    "execution_steps": [
                        *steps,
                        {"step": "public_comment", "state": "unknown", "reason": type(exc).__name__},
                    ],
                }
            )
            await self._store_redirect_state(session, record.id, failed)
            return RedirectPlanResponse(
                status="needs_review",
                redirect_plan_id=failed.id,
                plan_hash=failed.plan_hash,
                version=failed.version,
                execution_state=failed.execution_state,
            )

        comment_confirmed = approved.model_copy(
            update={
                "state": RedirectPlanState.executing,
                "execution_state": "comment_confirmed",
                "execution_steps": steps,
            }
        )
        await self._store_redirect_state(session, record.id, comment_confirmed)
        try:
            await self.client.update_task(task_id=ticket_id, status_id=30, auth_b64=token)
            fresh = await self.client.get_task(task_id=ticket_id, auth_b64=token)
            if fresh.status_id != 30:
                raise RuntimeError("cancel_status_not_confirmed")
        except Exception as exc:
            partial = comment_confirmed.model_copy(
                update={
                    "state": RedirectPlanState.needs_review,
                    "execution_state": "partial_unknown",
                    "execution_steps": [
                        *steps,
                        {"step": "cancel_status", "state": "unknown", "reason": type(exc).__name__},
                    ],
                }
            )
            await self._store_redirect_state(session, record.id, partial)
            return RedirectPlanResponse(
                status="needs_review",
                redirect_plan_id=partial.id,
                plan_hash=partial.plan_hash,
                version=partial.version,
                execution_state=partial.execution_state,
            )
        completed = comment_confirmed.model_copy(
            update={
                "state": RedirectPlanState.succeeded,
                "execution_state": "succeeded",
                "execution_steps": [
                    *steps,
                    {"step": "cancel_status", "state": "confirmed", "at": datetime.now(UTC).isoformat()},
                ],
            }
        )
        await self._store_redirect_state(session, record.id, completed)
        return RedirectPlanResponse(
            status="succeeded",
            redirect_plan_id=completed.id,
            plan_hash=completed.plan_hash,
            version=completed.version,
            execution_state=completed.execution_state,
        )

    async def correct_redirect(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: CorrectRedirectPlanRequest,
        operator: str,
        auth_b64: str | None,
    ) -> TicketAutomationDTO:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        record, plan = await self._locked_redirect(session, ticket_id, request)
        bundle = await self.repository.latest_for_task(session, ticket_id)
        version, entries, bindings = await self.catalog_repository.current(session)
        if bundle is None or version is None or version.catalog_hash != plan.catalog_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "service_catalog_changed")
        entry = next(
            (item for item in entries if item.service_id == request.target_service_id and item.is_active), None
        )
        binding = next(
            (
                item
                for item in bindings
                if request.target_service_id in item.service_ids
                and bundle.decision.primary_case_type in item.allowed_case_types
                and item.is_active
                and item.is_validated
            ),
            None,
        )
        if entry is None or binding is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "redirect_target_not_allowed")
        draft = plan.model_copy(
            update={
                "id": uuid4(),
                "target_service_id": entry.service_id,
                "target_service_path": entry.service_path,
                "binding_key": binding.key,
                "binding_version": binding.version,
                "rendered_public_comment": f"Заявка отменена, так как создана не в правильном разделе. Требуется оставить заявку в разделе: {entry.service_path}.",
                "version": plan.version + 1,
                "plan_hash": "",
            }
        )
        corrected = draft.model_copy(update={"plan_hash": compute_redirect_plan_hash(draft)})
        record.state = RedirectPlanState.rejected.value
        session.add(
            RedirectFeedbackRecord(
                redirect_plan_id=record.id,
                task_id=ticket_id,
                operator_username=operator,
                verdict="corrected",
                selected_target_service_id=entry.service_id,
                reason_tag=request.reason_tag,
                notes=request.notes,
            )
        )
        session.add(
            RedirectPlanRecord(
                id=corrected.id,
                task_id=corrected.task_id,
                snapshot_hash=corrected.snapshot_hash,
                case_decision_id=corrected.case_decision_id,
                compatibility_decision_id=corrected.compatibility_decision_id,
                source_service_id=corrected.source_service_id,
                target_service_id=corrected.target_service_id,
                target_service_path=corrected.target_service_path,
                strategy=corrected.strategy.value,
                template_key=corrected.template_key,
                rendered_public_comment=corrected.rendered_public_comment,
                catalog_hash=corrected.catalog_hash,
                binding_key=corrected.binding_key,
                binding_version=corrected.binding_version,
                plan_hash=corrected.plan_hash,
                state=corrected.state.value,
                approval_state=corrected.approval_state,
                execution_state=corrected.execution_state,
                version=corrected.version,
                plan_json=corrected.model_dump(mode="json"),
            )
        )
        await session.commit()
        refreshed = await self.repository.latest_for_task(session, ticket_id)
        if refreshed is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "automation_bundle_missing")
        return await self._dto(session, refreshed)

    async def stop_redirect(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        request: RedirectPlanRequest,
        operator: str,
        verdict: str,
        auth_b64: str | None,
    ) -> RedirectPlanResponse:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        record, plan = await self._locked_redirect(session, ticket_id, request)
        state = RedirectPlanState.rejected if verdict == "rejected" else RedirectPlanState.manual
        stopped = plan.model_copy(update={"state": state, "approval_state": verdict})
        record.state = state.value
        record.approval_state = verdict
        record.plan_json = stopped.model_dump(mode="json")
        session.add(
            RedirectFeedbackRecord(
                redirect_plan_id=record.id,
                task_id=ticket_id,
                operator_username=operator,
                verdict=verdict,
                selected_target_service_id=plan.target_service_id,
                reason_tag=request.reason_tag,
                notes=request.notes,
            )
        )
        await session.commit()
        return RedirectPlanResponse(
            status=verdict,
            redirect_plan_id=plan.id,
            plan_hash=plan.plan_hash,
            version=plan.version,
            execution_state=plan.execution_state,
        )

    async def _locked_redirect(self, session: AsyncSession, ticket_id: int, request: RedirectPlanRequest):
        record = await session.scalar(
            select(RedirectPlanRecord).where(RedirectPlanRecord.id == request.redirect_plan_id).with_for_update()
        )
        if record is None or record.task_id != ticket_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "redirect_plan_not_found")
        plan = RedirectPlan.model_validate(record.plan_json)
        if (
            record.plan_hash != request.plan_hash
            or record.snapshot_hash != request.snapshot_hash
            or record.version != request.version
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_or_tampered_redirect_plan")
        if record.state != RedirectPlanState.ready.value or record.approval_state != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, "redirect_plan_not_ready")
        return record, plan

    async def _store_redirect_state(self, session: AsyncSession, record_id, plan: RedirectPlan) -> None:
        record = await session.get(RedirectPlanRecord, record_id)
        if record is None:
            raise RuntimeError("redirect_plan_not_found")
        record.state = plan.state.value
        record.execution_state = plan.execution_state
        record.plan_json = plan.model_dump(mode="json")
        await session.commit()

    def _compile(
        self,
        frame,
        decision: CaseDecision,
        compatibility: ServiceCompatibilityDecision,
        binding=None,
        clarification_round: int = 0,
    ):
        if decision.state in {CaseDecisionState.selected, CaseDecisionState.multi_intent}:
            return self.compiler.compile(
                frame=frame,
                decision=decision,
                compatibility=compatibility,
                binding=binding,
                clarification_round=clarification_round,
            )
        workflow = WorkflowPlan(
            task_id=frame.task_id,
            snapshot_hash=frame.snapshot_hash,
            case_decision_id=decision.id,
            workflow_key="case_review_workflow",
            workflow_version="1.0.0",
            state=WorkflowPlanState.needs_review,
            disposition=Disposition.manual,
            reason_codes=[f"case_{decision.state.value}"],
        )
        return workflow, None

    def _compatibility_from_catalog(
        self,
        *,
        snapshot,
        decision: CaseDecision,
        catalog_hash: str | None,
        entries,
        bindings,
        target_resolution: TargetServiceResolution,
    ):
        compatibility = self.redirect_resolver.resolve(
            decision=decision,
            source_service_id=snapshot.service_id,
            source_task_type_id=snapshot.task_type_id,
            catalog_hash=catalog_hash,
            entries=entries,
            bindings=bindings,
            target_resolution=target_resolution,
        )
        binding = next(
            (
                item
                for item in bindings
                if item.key == compatibility.binding_key and item.version == compatibility.binding_version
            ),
            None,
        )
        return compatibility, binding

    async def sync_service_catalog(self, session: AsyncSession, *, auth_b64: str | None) -> dict[str, object]:
        token = await self._auth_token(auth_b64)
        try:
            version = await self.catalog_repository.sync(session, client=self.client, auth_b64=token)
        except ValueError as exc:
            await session.rollback()
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        await session.commit()
        return {
            "version": version.version,
            "catalog_hash": version.catalog_hash,
            "validation_state": version.validation_state.value,
            "is_active": version.is_active,
        }

    @staticmethod
    def _operator_frame(payload: dict[str, Any]):
        from core.automation.contracts import CaseFrame

        frame = CaseFrame.model_validate(payload)
        assertions = [
            item.model_copy(update={"extraction_method": ExtractionMethod.operator}) for item in frame.assertions
        ]
        return frame.model_copy(update={"assertions": assertions})

    async def _verify_current_snapshot(self, ticket_id: int, expected_hash: str, auth_b64: str | None) -> None:
        token = await self._auth_token(auth_b64)
        task = await self.client.get_task(task_id=ticket_id, auth_b64=token)
        lifetime = await self.client.get_task_lifetime(task_id=ticket_id, auth_b64=token)
        current = TicketSnapshotFactory.create(task, comments=lifetime)
        if current.snapshot_hash != expected_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_ticket_snapshot")

    async def _auth_token(self, auth_b64: str | None) -> str:
        if auth_b64:
            return auth_b64
        try:
            credentials = await self.auth_bootstrap.bootstrap_auth(
                client=self.client,
                redis_client=get_redis_client(),
                session_factory=async_session_factory,
            )
        except ServiceAuthError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "service_credentials_unavailable",
            ) from exc
        return credentials.auth_b64

    async def _dto(
        self,
        session: AsyncSession,
        bundle: AutomationBundle,
        *,
        suggested_corrections: list[SuggestedCorrectionDTO] | None = None,
    ) -> TicketAutomationDTO:
        if suggested_corrections is None:
            diag = bundle.workflow_plan.fact_extraction_diagnostics or {}
            diag_items = diag.get("suggested_corrections") or []
            suggested_corrections = [
                SuggestedCorrectionDTO.model_validate(c) for c in diag_items
            ]
        approval: dict[str, Any] = {}
        execution: list[dict[str, Any]] = []
        preflight: list[dict[str, Any]] = []
        if bundle.action_plan is not None:
            preflight_records = list(
                (
                    await session.scalars(
                        select(ActionPreflightRecord)
                        .where(ActionPreflightRecord.action_plan_id == bundle.action_plan.id)
                        .order_by(ActionPreflightRecord.created_at.desc())
                    )
                ).all()
            )
            preflight = [
                {
                    "action_id": item.action_id,
                    "capability_key": item.capability_key,
                    "status": item.status,
                    "checks": item.checks_json,
                    "details": item.details_json,
                    "error": item.error_message,
                    "expires_at": item.expires_at.isoformat(),
                }
                for item in preflight_records
            ]
            feedback = await session.scalar(
                select(PlanFeedbackRecord).where(PlanFeedbackRecord.action_plan_id == bundle.action_plan.id)
            )
            approval = {
                "state": feedback.verdict if feedback else "pending",
                "operator": feedback.operator_username if feedback else None,
                "execution_enabled": (
                    settings.AD_ONBOARDING_EXECUTION_ENABLED
                    if any(item.capability_key == "create_ad_user" for item in bundle.action_plan.actions)
                    else True
                ),
            }
            commands = list(
                (
                    await session.scalars(
                        select(CommandRecord)
                        .where(CommandRecord.action_plan_id == bundle.action_plan.id)
                        .order_by(CommandRecord.sequence_no.asc())
                    )
                ).all()
            )
            feedback_by_command = {
                item.command_id: item
                for item in (
                    await session.scalars(
                        select(ExecutionFeedbackRecord).where(
                            ExecutionFeedbackRecord.action_plan_id == bundle.action_plan.id
                        )
                    )
                ).all()
            }
            execution = [
                {
                    "command_id": str(command.id),
                    "action_id": command.action_id,
                    "capability_key": command.capability_key,
                    "status": command.status,
                    "outcome": (feedback_by_command[command.id].outcome if command.id in feedback_by_command else None),
                }
                for command in commands
            ]
        elif bundle.redirect_plan is not None:
            approval = {
                "state": bundle.redirect_plan.approval_state,
                "operator": None,
                "kind": "redirect",
            }
            execution = list(bundle.redirect_plan.execution_steps)
        compatibility = bundle.compatibility
        if compatibility.target_selection_state == TargetSelectionState.unavailable:
            version, entries, bindings = await self.catalog_repository.current(session)
            projected = self.redirect_resolver.resolve(
                decision=bundle.decision,
                source_service_id=compatibility.source_service_id,
                source_task_type_id=compatibility.source_task_type_id,
                catalog_hash=version.catalog_hash if version else None,
                entries=entries,
                bindings=bindings,
            )
            compatibility = compatibility.model_copy(
                update={
                    "source_service_path": projected.source_service_path,
                    "target_service_id": projected.target_service_id,
                    "target_service_path": projected.target_service_path,
                    "target_selection_state": projected.target_selection_state,
                    "target_selection_method": projected.target_selection_method,
                    "target_candidates": projected.target_candidates,
                    "authorization_state": projected.authorization_state,
                }
            )
        human_action_records = list(
            (
                await session.scalars(
                    select(HumanActionRequestRecord)
                    .where(HumanActionRequestRecord.task_id == bundle.frame.task_id)
                    .order_by(HumanActionRequestRecord.created_at.desc())
                )
            ).all()
        )
        human_actions_dto = [
            HumanActionRequestDTO(
                id=rec.id,
                task_id=rec.task_id,
                action_id=rec.action_id,
                target_param=rec.target_param,
                type=rec.type,
                prompt=rec.prompt,
                candidates=[
                    HumanActionCandidateDTO(
                        candidate_id=str(c.get("candidate_id", "")),
                        label=str(c.get("label", "")),
                        score=float(c.get("score", 0.0)),
                        evidence=list(c.get("evidence", [])),
                    )
                    for c in (rec.candidates_json or [])
                ],
                state=rec.state,
                selected_candidate_id=rec.selected_candidate_id,
                expires_at=rec.expires_at,
            )
            for rec in human_action_records
        ]
        readiness = await self._build_readiness(
            session,
            bundle=bundle,
            preflight=preflight,
            approval=approval,
            execution=execution,
            human_actions=human_action_records,
        )
        wf_def = self.workflows.get(bundle.workflow_plan.workflow_key)
        context_header: WorkflowContextHeaderDTO | None = None
        if wf_def is not None:
            pres = wf_def.presentation
            entities = bundle.frame.entities

            class SafeDict(dict):
                def __missing__(self, key: str) -> str:
                    return ""

            safe_entities = SafeDict(entities)
            subj_val = " ".join(pres.subject_template.format_map(safe_entities).split()).strip()
            subj_caption = (
                " ".join(pres.subject_caption_template.format_map(safe_entities).split()).strip()
                or None
            )
            context_header = WorkflowContextHeaderDTO(
                title=wf_def.name,
                workflow_key=wf_def.key,
                category_badge=pres.category_badge,
                icon=pres.icon,
                subject_label=pres.subject_label,
                subject_value=subj_val or "Не указан",
                subject_caption=subj_caption,
            )

        execution_summary: list[str] = []
        if bundle.action_plan and bundle.action_plan.actions:
            for action in bundle.action_plan.actions:
                spec = self.capabilities.get(action.capability_key)
                if spec is not None:
                    execution_summary.append(spec.format_summary(action.params))
                else:
                    execution_summary.append(action.capability_key)

        return TicketAutomationDTO(
            task_id=bundle.frame.task_id,
            snapshot_hash=bundle.frame.snapshot_hash,
            case_frame=bundle.frame,
            case_decision=bundle.decision,
            service_compatibility=compatibility,
            workflow_plan=bundle.workflow_plan,
            redirect_plan=bundle.redirect_plan,
            action_plan=bundle.action_plan,
            preflight=preflight,
            approval=approval,
            execution=execution,
            readiness=readiness,
            human_actions=human_actions_dto,
            context_header=context_header,
            execution_summary=execution_summary,
            suggested_corrections=suggested_corrections or [],
        )

    async def _build_readiness(
        self,
        session: AsyncSession,
        *,
        bundle: AutomationBundle,
        preflight: list[dict[str, Any]],
        approval: dict[str, Any],
        execution: list[dict[str, Any]],
        human_actions: list[HumanActionRequestRecord] | None = None,
    ) -> AutomationReadinessDTO | None:
        clarification = await session.scalar(
            select(ClarificationRequestRecord)
            .where(ClarificationRequestRecord.task_id == bundle.frame.task_id)
            .order_by(ClarificationRequestRecord.created_at.desc())
            .limit(1)
        )
        clarification_dto = None
        if clarification is not None:
            clarification_dto = ClarificationReadinessDTO(
                id=clarification.id,
                state=clarification.state,
                round=clarification.round,
                missing_facts=list(clarification.missing_facts_json or []),
                publish_attempts=int(clarification.publish_attempts or 0),
                last_error_code=clarification.last_error_code,
                last_error_detail=clarification.last_error_detail,
            )

        latest_preflight: dict[str, dict[str, Any]] = {}
        for item in preflight:
            latest_preflight.setdefault(str(item["action_id"]), item)
        plan_checks = list(latest_preflight.values())
        def _is_future(val: Any) -> bool:
            if val is None:
                return False
            dt = val if isinstance(val, datetime) else datetime.fromisoformat(str(val))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt > datetime.now(UTC)

        preflight_passed = (
            bool(bundle.action_plan)
            and len(plan_checks) == len(bundle.action_plan.actions)
            and all(
                item["status"] in {"passed", "not_applicable"}
                and _is_future(item.get("expires_at"))
                for item in plan_checks
            )
        )
        has_action_required = any(item.get("status") == "action_required" for item in plan_checks)
        preflight_failed = any(
            item["status"] not in {"passed", "not_applicable", "action_required"} for item in plan_checks
        )
        missing_facts = list(bundle.workflow_plan.missing_facts)
        pending_human_actions = [ha for ha in (human_actions or []) if ha.state == "pending"]

        blockers: list[ReadinessBlockerDTO] = []
        actions: list[str] = []
        if missing_facts:
            blockers.append(
                ReadinessBlockerDTO(
                    code="required_facts_missing",
                    stage="facts",
                    fact_keys=missing_facts,
                    retryable=True,
                    detail="Не получены обязательные данные.",
                )
            )
            actions.append("edit_facts")
        if clarification is not None and clarification.state == "failed":
            blockers.append(
                ReadinessBlockerDTO(
                    code="clarification_publish_failed",
                    stage="facts",
                    fact_keys=list(clarification.missing_facts_json or []),
                    retryable=True,
                    detail=clarification.last_error_detail,
                )
            )
            actions.append("retry_clarification")
        if pending_human_actions:
            for ha in pending_human_actions:
                blockers.append(
                    ReadinessBlockerDTO(
                        code="human_action_required",
                        stage="ou" if "ou" in ha.target_param else "hitl",
                        retryable=False,
                        detail=ha.prompt,
                    )
                )
            actions.append("resolve_human_action")
        if preflight_failed:
            blockers.append(
                ReadinessBlockerDTO(
                    code="preflight_failed",
                    stage="preflight",
                    retryable=True,
                    detail="Одна или несколько проверок перед созданием не пройдены.",
                )
            )
        execution_disabled = bool(bundle.action_plan) and approval.get("execution_enabled") is False
        if execution_disabled:
            is_ad = any(item.capability_key == "create_ad_user" for item in bundle.action_plan.actions)
            blockers.append(
                ReadinessBlockerDTO(
                    code="ad_onboarding_execution_disabled" if is_ad else "capability_execution_disabled",
                    stage="execution",
                    detail=(
                        "Создание в AD отключено настройкой AD_ONBOARDING_EXECUTION_ENABLED."
                        if is_ad
                        else "Автоматическое выполнение возможностей сценария отключено настройками безопасности."
                    ),
                )
            )

        command_states = {str(item.get("status")) for item in execution}
        completed = bundle.workflow_plan.state == WorkflowPlanState.completed or (
            bool(command_states) and command_states <= {"succeeded"}
        )
        running = bool(command_states & {"pending", "queued", "running"})

        is_onboarding = bundle.workflow_plan.workflow_key == "employee_onboarding_workflow"
        hitl_key = "ou" if is_onboarding else "hitl"
        has_delivery = (
            bool(bundle.action_plan and getattr(bundle.action_plan, "delivery_policy", None) is not None)
            or is_onboarding
        )
        has_hitl = is_onboarding or bool(pending_human_actions) or any(
            ha.state == "resolved" for ha in (human_actions or [])
        )

        stage_order: list[str] = ["facts"]
        if has_hitl:
            stage_order.append(hitl_key)
        stage_order.extend(["preflight", "approval", "execution"])
        if has_delivery:
            stage_order.append("delivery")

        stages = []
        for key in stage_order:
            if key == "facts":
                stage_state = "completed" if not missing_facts else "blocked"
            elif key in {"ou", "hitl"}:
                if pending_human_actions:
                    stage_state = "blocked"
                elif any(ha.state == "resolved" for ha in (human_actions or [])):
                    stage_state = "completed"
                elif missing_facts:
                    stage_state = "not_started"
                else:
                    stage_state = "completed"
            elif key == "preflight":
                if missing_facts or pending_human_actions:
                    stage_state = "not_started"
                elif preflight_passed:
                    stage_state = "completed"
                elif preflight_failed or has_action_required:
                    stage_state = "blocked"
                else:
                    stage_state = "active"
            elif key == "approval":
                if approval.get("state") == "approved":
                    stage_state = "completed"
                elif preflight_passed and approval.get("state") == "pending":
                    stage_state = "active"
                else:
                    stage_state = "not_started"
            elif key == "execution":
                if completed:
                    stage_state = "completed"
                elif running:
                    stage_state = "running"
                else:
                    stage_state = "not_started"
            elif key == "delivery":
                stage_state = "completed" if completed else "not_started"
            else:
                stage_state = "not_started"
            stages.append(ReadinessStageDTO(key=key, state=stage_state))

        if completed:
            state, current_stage = "completed", ("delivery" if has_delivery else "execution")
        elif running:
            state, current_stage = "running", "execution"
        elif missing_facts:
            state, current_stage = "blocked", "facts"
        elif pending_human_actions:
            state, current_stage = "blocked", (hitl_key if has_hitl else "facts")
        elif bundle.action_plan is None:
            state, current_stage = "blocked", "facts"
        elif not preflight_passed:
            state, current_stage = "blocked", "preflight"
        elif execution_disabled:
            state, current_stage = "blocked", "approval"
        elif approval.get("state") == "pending":
            state, current_stage = "ready_for_approval", "approval"
            actions.extend(["approve", "reject_automation"])
        else:
            state, current_stage = "blocked", "execution"

        if not completed and not running and "reanalyze" not in actions:
            actions.append("reanalyze")

        readiness_preflight = PreflightReadinessDTO(
            state=(
                "passed"
                if preflight_passed
                else "failed"
                if (preflight_failed or has_action_required)
                else "not_started"
            ),
            reason_code=("blocked_by_missing_facts" if bundle.action_plan is None and missing_facts else None),
            checks=[
                {
                    "action_id": item["action_id"],
                    "capability_key": item["capability_key"],
                    "status": item["status"],
                    "checks": item["checks"],
                    "details": item["details"],
                    "error": item.get("error"),
                }
                for item in plan_checks
            ],
        )
        return AutomationReadinessDTO(
            state=state,
            current_stage=current_stage,
            stages=stages,
            blockers=blockers,
            actions=list(dict.fromkeys(actions)),
            clarification=clarification_dto,
            preflight=readiness_preflight,
        )

    async def resolve_human_action(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        human_action_id: UUID,
        request: ResolveHumanActionRequest,
        operator: str,
        auth_b64: str | None,
    ) -> TicketAutomationDTO:
        await self._verify_current_snapshot(ticket_id, request.snapshot_hash, auth_b64)
        try:
            await self.human_action_resolution.resolve(
                session,
                ticket_id=ticket_id,
                human_action_id=human_action_id,
                candidate_id=request.candidate_id,
                operator=operator,
                generation=request.generation,
                snapshot_hash=request.snapshot_hash,
                plan_hash=request.plan_hash,
            )
        except HumanActionNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except HumanActionConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except HumanActionValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

        await session.commit()
        refreshed_bundle = await self.repository.latest_for_task(session, ticket_id)
        if refreshed_bundle is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "automation_bundle_missing")
        return await self._dto(session, refreshed_bundle)


def extract_operator(auth_b64: str | None) -> str:
    if not auth_b64:
        return "operator"
    try:
        decoded = base64.b64decode(auth_b64).decode("utf-8", errors="ignore")
        return decoded.split(":", 1)[0] if ":" in decoded else "operator"
    except Exception:
        return "operator"
