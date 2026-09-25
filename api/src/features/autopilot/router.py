import base64
import logging
from datetime import datetime, timezone
from typing import List, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.db import get_db_session
from api.src.core.redis import get_redis
from api.src.core.security import get_intraservice_auth
from core.autopilot.dto import AutopilotPolicyDTO, AutopilotPolicyUpdateDTO
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.models import CommandRecord

from .schemas import (
    AgentPlanDTO,
    ApprovePlanRequest,
    AutopilotCommandDTO,
    AutopilotCorrectionDTO,
    AutopilotPoliciesListResponse,
    AutopilotStatsResponse,
    CorrectPlanRequest,
    CorrectionsListResponse,
    UpdateAutopilotPolicyRequest,
)
from .service import AutopilotService

logger = logging.getLogger("api.features.autopilot")

router = APIRouter(prefix="/autopilot", tags=["Autopilot Governance"])


def get_policy_service_dep() -> AutopilotPolicyService:
    """Dependency provider for AutopilotPolicyService."""
    redis = get_redis()
    return AutopilotPolicyService(redis_client=redis)


def get_autopilot_service_dep() -> AutopilotService:
    """Dependency provider for AutopilotService."""
    return AutopilotService()


def _extract_username(auth_b64: Optional[str]) -> str:
    if not auth_b64:
        return "operator"
    try:
        decoded = base64.b64decode(auth_b64).decode("utf-8", errors="ignore")
        if ":" in decoded:
            return decoded.split(":", 1)[0]
    except Exception:
        pass
    return "operator"


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


@router.get("/plan/{ticket_id}", response_model=AgentPlanDTO)
async def get_agent_plan(
    ticket_id: int,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> AgentPlanDTO:
    """Synthesize complete agent evaluation plan for a ticket for supervisor inspection."""
    return await service.get_agent_plan(
        ticket_id=ticket_id,
        session=db,
        redis_client=redis,
        auth_b64=auth_b64,
    )


@router.post("/plan/{ticket_id}/approve")
async def approve_agent_plan(
    ticket_id: int,
    req: ApprovePlanRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> dict:
    """1-Click approve agent plan: verify optimistic lock, dispatch Taskiq command, log positive feedback."""
    operator = _extract_username(auth_b64)
    return await service.approve_plan(
        ticket_id=ticket_id,
        req=req,
        operator_username=operator,
        session=db,
        auth_b64=auth_b64,
    )


@router.post("/plan/{ticket_id}/correct")
async def correct_agent_plan(
    ticket_id: int,
    req: CorrectPlanRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> dict:
    """Correct agent plan: record Ground-Truth delta in AutopilotCorrection and dispatch corrected Taskiq task."""
    operator = _extract_username(auth_b64)
    return await service.correct_plan(
        ticket_id=ticket_id,
        req=req,
        operator_username=operator,
        session=db,
        auth_b64=auth_b64,
    )


@router.get("/corrections", response_model=CorrectionsListResponse)
async def list_corrections(
    limit: int = Query(default=100, ge=1, le=500),
    tag: Optional[str] = Query(default=None),
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> CorrectionsListResponse:
    """Retrieve historical supervisor corrections dataset for inspection."""
    records = await service.list_corrections(limit=limit, tag=tag, session=db)
    return CorrectionsListResponse(corrections=records, total=len(records))


@router.get("/corrections/export")
async def export_corrections_jsonl(
    limit: int = Query(default=500, ge=1, le=2000),
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> Response:
    """Export Ground-Truth correction dataset in JSONL format for Harness AI coder."""
    jsonl_content = await service.export_corrections_jsonl(limit=limit, session=db)
    filename = f"autopilot_corrections_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
    return Response(
        content=jsonl_content.encode("utf-8"),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
