"""Application service for ADR 0006 case/workflow/capability automation."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.config import settings
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
from core.automation.snapshot import TicketSnapshotFactory
from core.automation.workflows import WorkflowRegistry, get_default_workflow_registry
from core.database.models import (
    ActionPlanRecord,
    CaseDecisionRecord,
    CaseFeedbackRecord,
    CommandRecord,
    ExecutionFeedbackRecord,
    PlanFeedbackRecord,
)
from core.intraservice.auth import ServiceAuthBootstrap
from core.intraservice.client import IntraServiceClient

from .schemas import (
    ActionPlanFeedbackRequest,
    ApprovalResponse,
    ApproveActionPlanRequest,
    CorrectActionPlanRequest,
    CorrectCaseDecisionRequest,
    FeedbackResponse,
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
        token = await self._auth_token(auth_b64)
        task = await self.client.get_task(task_id=ticket_id, auth_b64=token)
        lifetime = await self.client.get_task_lifetime(task_id=ticket_id, auth_b64=token)
        snapshot = TicketSnapshotFactory.create(task, comments=lifetime)
        latest = await self.repository.latest_for_task(session, ticket_id)
        if not force and latest is not None and latest.frame.snapshot_hash == snapshot.snapshot_hash:
            return await self._dto(session, latest)

        frame, decision = await self.intake.analyze(snapshot)
        workflow, action_plan = self._compile(frame, decision)
        bundle = await self.repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=action_plan,
        )
        if action_plan is not None:
            await self.preflight.run_plan(session, action_plan_id=action_plan.id)
        await session.commit()
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
            await self.workflow_policy.require_assisted(
                session, workflow_key=plan_for_policy.workflow_key
            )
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
                    select(ActionPlanRecord)
                    .where(ActionPlanRecord.case_decision_id == original.id)
                    .with_for_update()
                )
            ).all()
        )
        if any(item.state in {ActionPlanState.approved.value, ActionPlanState.running.value} for item in previous_plans):
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
        workflow, action_plan = self._compile(frame, decision)
        bundle = await self.repository.save_analysis(
            session,
            frame=frame,
            decision=decision,
            workflow_plan=workflow,
            action_plan=action_plan,
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

    def _compile(self, frame, decision: CaseDecision):
        if decision.state in {CaseDecisionState.selected, CaseDecisionState.multi_intent}:
            return self.compiler.compile(frame=frame, decision=decision)
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

    @staticmethod
    def _operator_frame(payload: dict[str, Any]):
        from core.automation.contracts import CaseFrame

        frame = CaseFrame.model_validate(payload)
        assertions = [
            item.model_copy(update={"extraction_method": ExtractionMethod.operator})
            for item in frame.assertions
        ]
        return frame.model_copy(update={"assertions": assertions})

    async def _verify_current_snapshot(
        self, ticket_id: int, expected_hash: str, auth_b64: str | None
    ) -> None:
        token = await self._auth_token(auth_b64)
        task = await self.client.get_task(task_id=ticket_id, auth_b64=token)
        lifetime = await self.client.get_task_lifetime(task_id=ticket_id, auth_b64=token)
        current = TicketSnapshotFactory.create(task, comments=lifetime)
        if current.snapshot_hash != expected_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "stale_ticket_snapshot")

    async def _auth_token(self, auth_b64: str | None) -> str:
        if auth_b64:
            return auth_b64
        credentials = await self.auth_bootstrap.bootstrap_auth(client=self.client)
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
                    "outcome": (
                        feedback_by_command[command.id].outcome
                        if command.id in feedback_by_command
                        else None
                    ),
                }
                for command in commands
            ]
        return TicketAutomationDTO(
            task_id=bundle.frame.task_id,
            snapshot_hash=bundle.frame.snapshot_hash,
            case_frame=bundle.frame,
            case_decision=bundle.decision,
            workflow_plan=bundle.workflow_plan,
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
