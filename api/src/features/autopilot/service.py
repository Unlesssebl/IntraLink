"""Application service for ADR 0006 case/workflow/capability automation."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

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
from core.automation.workflows import WorkflowRegistry, get_default_workflow_registry
from core.database.models import (
    ActionPlanRecord,
    CaseDecisionRecord,
    CaseFeedbackRecord,
    CommandRecord,
    ExecutionFeedbackRecord,
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
    CorrectActionPlanRequest,
    CorrectCaseDecisionRequest,
    CorrectRedirectPlanRequest,
    CorrectTargetServiceRequest,
    FeedbackResponse,
    RedirectPlanRequest,
    RedirectPlanResponse,
    TicketAutomationDTO,
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
        self.runner = WorkflowRunner(self.repository)
        self.workflow_policy = WorkflowPolicyService()
        self.catalog_repository = ServiceCatalogRepository()
        self.redirect_resolver = RedirectResolver()
        self.target_reranker = target_reranker or LLMServiceTargetReranker(
            transport,
            model_alias=settings.LITELLM_MODEL_FAST,
        )

    async def get_automation(self, session: AsyncSession, ticket_id: int) -> TicketAutomationDTO:
        bundle = await self.repository.latest_for_task(session, ticket_id)
        if bundle is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "automation_not_analyzed")
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
            return await self._dto(session, latest)

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
        timings["case_classification"] = round((perf_counter() - intake_started) * 1000)
        compatibility, binding = self._compatibility_from_catalog(
            snapshot=snapshot,
            decision=decision,
            catalog_hash=version.catalog_hash if version else None,
            entries=entries,
            bindings=bindings,
            target_resolution=target_resolution,
        )
        llm_used = target_resolution.llm_used or frame.llm_attempted or any(
            item.extraction_method == ExtractionMethod.llm for item in frame.assertions
        ) or bool(decision.verifier_trace)
        compatibility = compatibility.model_copy(
            update={
                "analysis_timings_ms": timings,
                "llm_used": llm_used,
            }
        )
        clarification_round = 0
        if latest is not None and latest.workflow_plan.state == WorkflowPlanState.awaiting_facts:
            clarification_round = latest.workflow_plan.clarification_round + 1
        workflow, action_plan = self._compile(frame, decision, compatibility, binding, clarification_round)
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
            question = (
                "Для создания учётной записи дополнительно укажите, пожалуйста: "
                + ", ".join(workflow.missing_facts)
                + "."
            )
            try:
                await self.client.update_task(
                    task_id=ticket_id,
                    status_id=6,
                    comment=question,
                    is_private=False,
                    auth_b64=token,
                )
            except Exception as exc:
                record = await session.get(WorkflowPlanRecord, workflow.id)
                if record is not None:
                    failed = workflow.model_copy(
                        update={
                            "state": WorkflowPlanState.needs_review,
                            "disposition": Disposition.manual,
                            "missing_facts": [],
                            "reason_codes": [
                                *workflow.reason_codes,
                                f"clarification_publish_failed:{type(exc).__name__}",
                            ],
                        }
                    )
                    record.state = failed.state.value
                    record.disposition = failed.disposition.value
                    record.plan_json = failed.model_dump(mode="json")
                    await session.commit()
                    bundle = AutomationBundle(
                        bundle.frame,
                        bundle.decision,
                        bundle.compatibility,
                        failed,
                        bundle.action_plan,
                        bundle.redirect_plan,
                    )
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
            item.service_id: item
            for item in entries
            if item.is_active and item.catalog_hash == version.catalog_hash
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

    async def _dto(self, session: AsyncSession, bundle: AutomationBundle) -> TicketAutomationDTO:
        approval: dict[str, Any] = {}
        execution: list[dict[str, Any]] = []
        if bundle.action_plan is not None:
            feedback = await session.scalar(
                select(PlanFeedbackRecord).where(PlanFeedbackRecord.action_plan_id == bundle.action_plan.id)
            )
            approval = {
                "state": feedback.verdict if feedback else "pending",
                "operator": feedback.operator_username if feedback else None,
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
        return TicketAutomationDTO(
            task_id=bundle.frame.task_id,
            snapshot_hash=bundle.frame.snapshot_hash,
            case_frame=bundle.frame,
            case_decision=bundle.decision,
            service_compatibility=compatibility,
            workflow_plan=bundle.workflow_plan,
            redirect_plan=bundle.redirect_plan,
            action_plan=bundle.action_plan,
            approval=approval,
            execution=execution,
        )


def extract_operator(auth_b64: str | None) -> str:
    if not auth_b64:
        return "operator"
    try:
        decoded = base64.b64decode(auth_b64).decode("utf-8", errors="ignore")
        return decoded.split(":", 1)[0] if ":" in decoded else "operator"
    except Exception:
        return "operator"
