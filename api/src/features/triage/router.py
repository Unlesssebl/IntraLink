"""Triage feature router."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.db import get_db_session

from .schemas import (
    ApplyDecisionRequest,
    BatchTriageRequest,
    BatchTriageResponse,
    TriageAnalysisResponse,
    TriageQueueResponse,
)
from .service import TriageService

router = APIRouter(prefix="/triage", tags=["Triage"])


def get_triage_service() -> TriageService:
    return TriageService()


@router.get("/queue", response_model=TriageQueueResponse)
async def get_triage_queue(
    filter_id: int = Query(default=984, description="Queue filter ID"),
    limit: int = Query(default=100, ge=1, le=500),
    service: TriageService = Depends(get_triage_service),
) -> TriageQueueResponse:
    """Retrieve 1st line queue tickets for triage inspection."""
    return await service.get_queue(filter_id=filter_id, limit=limit)


@router.post("/analyze/{ticket_id}", response_model=TriageAnalysisResponse)
async def analyze_ticket(
    ticket_id: int,
    service: TriageService = Depends(get_triage_service),
    db: AsyncSession = Depends(get_db_session),
) -> TriageAnalysisResponse:
    """Perform deterministic rule and LLM analysis on a ticket."""
    return await service.analyze_ticket(ticket_id=ticket_id, session=db)


@router.post("/batch-analyze", response_model=BatchTriageResponse)
async def batch_analyze_queue(
    req: BatchTriageRequest,
    service: TriageService = Depends(get_triage_service),
    db: AsyncSession = Depends(get_db_session),
) -> BatchTriageResponse:
    """Batch analyze queue with duplicate detection."""
    return await service.batch_analyze(filter_id=req.filter_id, limit=req.limit, session=db)


@router.post("/apply/{audit_id}")
async def apply_triage_decision(
    audit_id: uuid.UUID,
    req: ApplyDecisionRequest,
    service: TriageService = Depends(get_triage_service),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Apply approved triage action to IntraService ticket."""
    try:
        return await service.apply_decision(audit_id=audit_id, req=req, session=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
