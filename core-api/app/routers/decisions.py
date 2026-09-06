"""Read and review durable triage/autopilot decisions."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import DecisionFeedback, DecisionRecord, DecisionStep, get_db
from app.routers.deps import principal_subject, require_permission, verify_trusted_origin
from app.services.decision_journal import DecisionJournalService, serialize_decision


router = APIRouter(prefix="/api/v2", tags=["Decision journal"])


class FeedbackRequest(BaseModel):
    verdict: Literal["accepted", "modified", "rejected", "correct", "partial", "incorrect", "insufficient_data"]
    reason_code: str | None = Field(None, max_length=64)
    comment: str | None = Field(None, max_length=2_000)
    final_action: dict[str, Any] = Field(default_factory=dict)


@router.get("/tasks/{task_id}/decisions")
async def list_task_decisions(
    task_id: int,
    limit: int = Query(20, ge=1, le=100),
    before_version: int | None = Query(None, ge=1),
    _triage=Depends(require_permission("triage:read")),
    _audit=Depends(require_permission("audit:read")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(DecisionRecord).where(DecisionRecord.task_id == task_id)
    if before_version is not None:
        stmt = stmt.where(DecisionRecord.version < before_version)
    records = list(
        (await db.scalars(stmt.order_by(desc(DecisionRecord.version)).limit(limit))).all()
    )
    return {
        "items": [serialize_decision(item) for item in records],
        "next_before_version": records[-1].version if len(records) == limit else None,
    }


@router.get("/decisions/{decision_id}")
async def get_decision(
    decision_id: uuid.UUID,
    _triage=Depends(require_permission("triage:read")),
    _audit=Depends(require_permission("audit:read")),
    db: AsyncSession = Depends(get_db),
):
    record = await db.get(DecisionRecord, decision_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "decision_not_found")
    steps = list(
        (
            await db.scalars(
                select(DecisionStep)
                .where(DecisionStep.decision_id == decision_id)
                .order_by(DecisionStep.sequence)
            )
        ).all()
    )
    feedback = list(
        (
            await db.scalars(
                select(DecisionFeedback)
                .where(DecisionFeedback.decision_id == decision_id)
                .order_by(DecisionFeedback.created_at)
            )
        ).all()
    )
    result = serialize_decision(record, steps=steps)
    result["feedback"] = [
        {
            "id": str(item.id),
            "verdict": item.verdict,
            "reason_code": item.reason_code,
            "comment": item.comment,
            "final_action": item.final_action_json,
            "actor": item.actor,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in feedback
    ]
    return result


@router.post("/decisions/{decision_id}/feedback", status_code=status.HTTP_201_CREATED)
async def create_feedback(
    decision_id: uuid.UUID,
    payload: FeedbackRequest,
    actor: str = Depends(principal_subject),
    _permission=Depends(require_permission("triage:mutate")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    item = await DecisionJournalService(db).add_feedback(
        decision_id=decision_id,
        verdict=payload.verdict,
        reason_code=payload.reason_code,
        comment=payload.comment,
        final_action=payload.final_action,
        actor=actor,
    )
    return {
        "id": str(item.id),
        "decision_id": str(item.decision_id),
        "verdict": item.verdict,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
