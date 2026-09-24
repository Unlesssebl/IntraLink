"""Triage feature router."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.db import get_db_session
from api.src.core.security import get_intraservice_auth

from .schemas import (
    ApplyDecisionRequest,
    BatchTriageRequest,
    BatchTriageResponse,
    TriageAnalysisResponse,
)
from .service import TriageService

router = APIRouter(prefix="/triage", tags=["Triage"])


def get_triage_service() -> TriageService:
    return TriageService()


@router.post("/analyze/{ticket_id}", response_model=TriageAnalysisResponse)
async def analyze_ticket(
    ticket_id: int,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TriageService = Depends(get_triage_service),
    db: AsyncSession = Depends(get_db_session),
) -> TriageAnalysisResponse:
    """Perform deterministic rule and LLM analysis on a ticket."""
    return await service.analyze_ticket(ticket_id=ticket_id, session=db, auth_b64=auth_b64)


@router.post("/batch-analyze", response_model=BatchTriageResponse)
async def batch_analyze_queue(
    req: BatchTriageRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TriageService = Depends(get_triage_service),
    db: AsyncSession = Depends(get_db_session),
) -> BatchTriageResponse:
    """Batch analyze queue with duplicate detection."""
    return await service.batch_analyze(
        filter_id=req.filter_id,
        limit=req.limit,
        session=db,
        auth_b64=auth_b64,
    )


@router.post("/apply/{audit_id}")
async def apply_triage_decision(
    audit_id: uuid.UUID,
    req: ApplyDecisionRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TriageService = Depends(get_triage_service),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Apply approved triage action to IntraService ticket."""
    try:
        return await service.apply_decision(
            audit_id=audit_id,
            req=req,
            session=db,
            auth_b64=auth_b64,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
