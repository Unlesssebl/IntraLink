"""Autopilot Governance REST API router."""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.db import get_db_session
from api.src.core.redis import get_redis
from api.src.core.security import get_intraservice_auth
from core.autopilot.dto import AutopilotPolicyDTO, AutopilotPolicyUpdateDTO
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.models import CommandRecord

from .schemas import (
    AutopilotCommandDTO,
    AutopilotPoliciesListResponse,
    AutopilotStatsResponse,
    UpdateAutopilotPolicyRequest,
)

logger = logging.getLogger("api.features.autopilot")

router = APIRouter(prefix="/autopilot", tags=["Autopilot Governance"])


def get_policy_service_dep() -> AutopilotPolicyService:
    """Dependency provider for AutopilotPolicyService."""
    redis = get_redis()
    return AutopilotPolicyService(redis_client=redis)


@router.get("/policies", response_model=AutopilotPoliciesListResponse)
async def list_policies(
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    policy_service: AutopilotPolicyService = Depends(get_policy_service_dep),
) -> AutopilotPoliciesListResponse:
    """Retrieve all scenario autopilot policies and tripwire statuses."""
    policies = await policy_service.list_policies(session=db)
    return AutopilotPoliciesListResponse(policies=policies, total=len(policies))


@router.put("/policies/{scenario_key}", response_model=AutopilotPolicyDTO)
async def update_policy(
    scenario_key: str,
    req: UpdateAutopilotPolicyRequest,
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    policy_service: AutopilotPolicyService = Depends(get_policy_service_dep),
) -> AutopilotPolicyDTO:
    """Update scenario autonomy mode and confidence threshold (resets circuit breaker)."""
    clean_key = scenario_key.strip().lower()
    try:
        dto = AutopilotPolicyUpdateDTO(mode=req.mode, min_confidence=req.min_confidence)
        updated = await policy_service.update_policy(
            scenario_key=clean_key,
            update_dto=dto,
            session=db,
        )
        return updated
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/policies/{scenario_key}/reset", response_model=AutopilotPolicyDTO)
async def reset_circuit_breaker(
    scenario_key: str,
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    policy_service: AutopilotPolicyService = Depends(get_policy_service_dep),
) -> AutopilotPolicyDTO:
    """Manually reset circuit breaker tripwire and restore scenario autonomy."""
    clean_key = scenario_key.strip().lower()
    # Reset failure counter
    await policy_service.record_success(scenario_key=clean_key, session=db)
    # Fetch updated state
    return await policy_service.get_policy(scenario_key=clean_key, session=db)


@router.get("/commands", response_model=List[AutopilotCommandDTO])
async def list_commands(
    limit: int = Query(default=50, ge=1, le=200),
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
) -> List[AutopilotCommandDTO]:
    """Retrieve recent autopilot CommandRecord entries for Live Feed."""
    stmt = (
        select(CommandRecord)
        .order_by(CommandRecord.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    records = result.scalars().all()
    return [
        AutopilotCommandDTO(
            id=r.id,
            action=r.action,
            status=r.status,
            task_id=r.task_id,
            initiator=r.initiator,
            target_json=r.target_json,
            error_message=r.error_message,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in records
    ]


@router.get("/stats", response_model=AutopilotStatsResponse)
async def get_autopilot_stats(
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    policy_service: AutopilotPolicyService = Depends(get_policy_service_dep),
) -> AutopilotStatsResponse:
    """Calculate aggregate efficiency and circuit breaker telemetry."""
    # Count succeeded commands
    stmt = select(func.count(CommandRecord.id)).where(CommandRecord.status == "succeeded")
    res = await db.execute(stmt)
    succeeded_count = res.scalar() or 0

    policies = await policy_service.list_policies(session=db)
    active_count = sum(1 for p in policies if p.mode != "DISABLED")
    tripped_count = sum(1 for p in policies if p.is_circuit_broken)

    # Average 0.25 hours (15 mins) saved per automated command
    hours_saved = round(succeeded_count * 0.25, 1)

    return AutopilotStatsResponse(
        total_automated_actions=succeeded_count,
        hours_saved=hours_saved,
        active_scenarios_count=active_count,
        tripped_circuit_breakers=tripped_count,
    )
