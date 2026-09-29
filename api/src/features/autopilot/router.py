"""ADR 0006 automation HTTP API. Legacy scenario endpoints are intentionally absent."""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.config import settings
from api.src.core.db import get_db_session
from api.src.core.redis import get_redis
from api.src.core.security import get_intraservice_auth
from core.automation.capabilities import get_default_capability_registry
from core.automation.case_profiles import CaseProfileRegistry
from core.automation.workflows import get_default_workflow_registry
from core.database.models import CommandRecord
from core.redis_lock import DistributedTaskLock

from .schemas import (
    ActionPlanFeedbackRequest,
    ApprovalResponse,
    ApproveActionPlanRequest,
    AutomationCommandDTO,
    CatalogResponse,
    CorrectActionPlanRequest,
    CorrectCaseDecisionRequest,
    CorrectOnboardingFactsRequest,
    CorrectRedirectPlanRequest,
    CorrectTargetServiceRequest,
    FeedbackResponse,
    RedirectPlanRequest,
    RedirectPlanResponse,
    ResumeClarificationRequest,
    RetryClarificationRequest,
    TicketAutomationDTO,
)
from .service import AutomationService, extract_operator

router = APIRouter(prefix="/autopilot", tags=["Ticket automation"])
internal_router = APIRouter(prefix="/internal/autopilot", tags=["Internal ticket automation"])


def get_automation_service() -> AutomationService:
    return AutomationService()


Db = Annotated[AsyncSession, Depends(get_db_session)]
Auth = Annotated[str | None, Depends(get_intraservice_auth)]
Service = Annotated[AutomationService, Depends(get_automation_service)]


@router.get("/tickets/{ticket_id}/automation", response_model=TicketAutomationDTO | None)
async def get_ticket_automation(
    ticket_id: int, db: Db, _auth: Auth, service: Service
) -> TicketAutomationDTO | None:
    return await service.get_automation(db, ticket_id)


async def _analyze_with_lock(
    ticket_id: int,
    *,
    force: bool,
    db: AsyncSession,
    auth: str | None,
    service: AutomationService,
    redis_client: aioredis.Redis,
) -> TicketAutomationDTO:
    lock = DistributedTaskLock(redis_client, f"lock:analysis:{ticket_id}", ttl_seconds=60)
    if not await lock.acquire():
        raise HTTPException(status.HTTP_409_CONFLICT, "analysis_already_running")
    try:
        return await service.analyze(db, ticket_id=ticket_id, auth_b64=auth, force=force)
    finally:
        await lock.release()


@router.post("/tickets/{ticket_id}/analyze", response_model=TicketAutomationDTO)
async def analyze_ticket(
    ticket_id: int,
    db: Db,
    auth: Auth,
    service: Service,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
) -> TicketAutomationDTO:
    return await _analyze_with_lock(
        ticket_id, force=False, db=db, auth=auth, service=service, redis_client=redis_client
    )


@router.post("/tickets/{ticket_id}/reanalyze", response_model=TicketAutomationDTO)
async def reanalyze_ticket(
    ticket_id: int,
    db: Db,
    auth: Auth,
    service: Service,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
) -> TicketAutomationDTO:
    return await _analyze_with_lock(
        ticket_id, force=True, db=db, auth=auth, service=service, redis_client=redis_client
    )


@internal_router.post("/tickets/{ticket_id}/resume", response_model=TicketAutomationDTO)
async def resume_ticket(
    ticket_id: int,
    request: ResumeClarificationRequest,
    db: Db,
    service: Service,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
    worker_key: Annotated[str | None, Header(alias="X-Worker-Key")] = None,
) -> TicketAutomationDTO:
    configured = settings.WORKER_API_KEY
    if not configured or not worker_key or not hmac.compare_digest(worker_key, configured):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_worker_key")
    lock = DistributedTaskLock(redis_client, f"lock:analysis:{ticket_id}", ttl_seconds=60)
    if not await lock.acquire():
        raise HTTPException(status.HTTP_409_CONFLICT, "analysis_already_running")
    try:
        return await service.resume(db, ticket_id=ticket_id, event_id=request.event_id)
    finally:
        await lock.release()


@router.post("/tickets/{ticket_id}/case-decision/correct", response_model=TicketAutomationDTO)
async def correct_case_decision(
    ticket_id: int,
    request: CorrectCaseDecisionRequest,
    db: Db,
    auth: Auth,
    service: Service,
) -> TicketAutomationDTO:
    return await service.correct_case(
        db,
        ticket_id=ticket_id,
        request=request,
        operator=extract_operator(auth),
        auth_b64=auth,
    )


@router.post("/tickets/{ticket_id}/onboarding-facts/correct", response_model=TicketAutomationDTO)
async def correct_onboarding_facts(
    ticket_id: int,
    request: CorrectOnboardingFactsRequest,
    db: Db,
    auth: Auth,
    service: Service,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
) -> TicketAutomationDTO:
    lock = DistributedTaskLock(redis_client, f"lock:analysis:{ticket_id}", ttl_seconds=60)
    if not await lock.acquire():
        raise HTTPException(status.HTTP_409_CONFLICT, "analysis_already_running")
    try:
        return await service.correct_onboarding_facts(
            db,
            ticket_id=ticket_id,
            request=request,
            operator=extract_operator(auth),
            auth_b64=auth,
        )
    finally:
        await lock.release()


@router.post(
    "/tickets/{ticket_id}/clarifications/{clarification_id}/retry",
    response_model=TicketAutomationDTO,
)
async def retry_clarification(
    ticket_id: int,
    clarification_id: UUID,
    request: RetryClarificationRequest,
    db: Db,
    auth: Auth,
    service: Service,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
) -> TicketAutomationDTO:
    lock = DistributedTaskLock(redis_client, f"lock:analysis:{ticket_id}", ttl_seconds=60)
    if not await lock.acquire():
        raise HTTPException(status.HTTP_409_CONFLICT, "analysis_already_running")
    try:
        return await service.retry_clarification(
            db,
            ticket_id=ticket_id,
            clarification_id=clarification_id,
            request=request,
            auth_b64=auth,
        )
    finally:
        await lock.release()


@router.post("/tickets/{ticket_id}/target-service/correct", response_model=TicketAutomationDTO)
async def correct_target_service(
    ticket_id: int,
    request: CorrectTargetServiceRequest,
    db: Db,
    auth: Auth,
    service: Service,
) -> TicketAutomationDTO:
    return await service.correct_target_service(
        db,
        ticket_id=ticket_id,
        request=request,
        operator=extract_operator(auth),
        auth_b64=auth,
    )


@router.post("/tickets/{ticket_id}/action-plans/approve", response_model=ApprovalResponse)
async def approve_action_plan(
    ticket_id: int,
    request: ApproveActionPlanRequest,
    db: Db,
    auth: Auth,
    service: Service,
) -> ApprovalResponse:
    return await service.approve(
        db,
        ticket_id=ticket_id,
        request=request,
        operator=extract_operator(auth),
        auth_b64=auth,
    )


@router.post("/tickets/{ticket_id}/action-plans/correct", response_model=TicketAutomationDTO)
async def correct_action_plan(
    ticket_id: int,
    request: CorrectActionPlanRequest,
    db: Db,
    auth: Auth,
    service: Service,
) -> TicketAutomationDTO:
    return await service.correct_action_plan(
        db,
        ticket_id=ticket_id,
        request=request,
        operator=extract_operator(auth),
        auth_b64=auth,
    )


@router.post("/tickets/{ticket_id}/action-plans/reject", response_model=FeedbackResponse)
async def reject_action_plan(
    ticket_id: int,
    request: ActionPlanFeedbackRequest,
    db: Db,
    auth: Auth,
    service: Service,
) -> FeedbackResponse:
    return await service.record_plan_feedback(
        db,
        ticket_id=ticket_id,
        request=request,
        operator=extract_operator(auth),
        verdict="rejected",
        auth_b64=auth,
    )


@router.post("/tickets/{ticket_id}/action-plans/manual-takeover", response_model=FeedbackResponse)
async def manual_takeover(
    ticket_id: int,
    request: ActionPlanFeedbackRequest,
    db: Db,
    auth: Auth,
    service: Service,
) -> FeedbackResponse:
    return await service.record_plan_feedback(
        db,
        ticket_id=ticket_id,
        request=request,
        operator=extract_operator(auth),
        verdict="manual_takeover",
        auth_b64=auth,
    )


@router.post("/tickets/{ticket_id}/redirect-plans/approve", response_model=RedirectPlanResponse)
async def approve_redirect_plan(
    ticket_id: int, request: RedirectPlanRequest, db: Db, auth: Auth, service: Service
) -> RedirectPlanResponse:
    return await service.approve_redirect(
        db, ticket_id=ticket_id, request=request, operator=extract_operator(auth), auth_b64=auth
    )


@router.post("/tickets/{ticket_id}/redirect-plans/correct", response_model=TicketAutomationDTO)
async def correct_redirect_plan(
    ticket_id: int, request: CorrectRedirectPlanRequest, db: Db, auth: Auth, service: Service
) -> TicketAutomationDTO:
    return await service.correct_redirect(
        db, ticket_id=ticket_id, request=request, operator=extract_operator(auth), auth_b64=auth
    )


@router.post("/tickets/{ticket_id}/redirect-plans/reject", response_model=RedirectPlanResponse)
async def reject_redirect_plan(
    ticket_id: int, request: RedirectPlanRequest, db: Db, auth: Auth, service: Service
) -> RedirectPlanResponse:
    return await service.stop_redirect(
        db, ticket_id=ticket_id, request=request, operator=extract_operator(auth), verdict="rejected", auth_b64=auth
    )


@router.post("/tickets/{ticket_id}/redirect-plans/manual-takeover", response_model=RedirectPlanResponse)
async def manual_redirect_plan(
    ticket_id: int, request: RedirectPlanRequest, db: Db, auth: Auth, service: Service
) -> RedirectPlanResponse:
    return await service.stop_redirect(
        db, ticket_id=ticket_id, request=request, operator=extract_operator(auth), verdict="manual", auth_b64=auth
    )


@router.post("/service-catalog/sync")
async def sync_service_catalog(db: Db, auth: Auth, service: Service) -> dict[str, object]:
    return await service.sync_service_catalog(db, auth_b64=auth)


@router.get("/case-types", response_model=CatalogResponse)
async def list_case_types(_auth: Auth) -> CatalogResponse:
    items = [item.model_dump(mode="json") for item in CaseProfileRegistry().list_all()]
    return CatalogResponse(items=items, total=len(items))


@router.get("/workflows", response_model=CatalogResponse)
async def list_workflows(_auth: Auth) -> CatalogResponse:
    items = [item.model_dump(mode="json") for item in get_default_workflow_registry().list_all()]
    return CatalogResponse(items=items, total=len(items))


@router.get("/capabilities", response_model=CatalogResponse)
async def list_capabilities(_auth: Auth) -> CatalogResponse:
    items = [item.model_dump(mode="json") for item in get_default_capability_registry().list_all()]
    return CatalogResponse(items=items, total=len(items))


@router.get("/commands", response_model=list[AutomationCommandDTO])
async def list_commands(
    db: Db,
    _auth: Auth,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AutomationCommandDTO]:
    records = list(
        (await db.scalars(select(CommandRecord).order_by(CommandRecord.created_at.desc()).limit(limit))).all()
    )
    return [AutomationCommandDTO.model_validate(record, from_attributes=True) for record in records]


@router.get("/commands/{command_id}", response_model=AutomationCommandDTO)
async def get_command(command_id: UUID, db: Db, _auth: Auth) -> AutomationCommandDTO:
    record = await db.get(CommandRecord, command_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "command_not_found")
    return AutomationCommandDTO.model_validate(record, from_attributes=True)
