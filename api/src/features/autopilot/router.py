"""Autopilot API Router for Evidence-Based Routing Cascade and Operator Supervision."""

import base64
import json
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
    ApprovePlanResponse,
    AutopilotCommandDTO,
    AutopilotPoliciesListResponse,
    AutopilotStatsResponse,
    BatchAssignRequest,
    BatchAssignResponse,
    CorrectPlanRequest,
    CorrectPlanResponse,
    FeedbackListResponse,
    ManualTakeoverRequest,
    ManualTakeoverResponse,
    RejectPlanRequest,
    RejectPlanResponse,
    RoutingQualityMetricsResponse,
    ScenarioCatalogResponse,
    UpdateAutopilotPolicyRequest,
)
from .service import AutopilotService

logger = logging.getLogger("api.features.autopilot")

router = APIRouter(prefix="/autopilot", tags=["Autopilot Governance"])


def get_policy_service_dep(
    redis: aioredis.Redis = Depends(get_redis),
) -> AutopilotPolicyService:
    """Dependency provider for AutopilotPolicyService."""
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
    """Update scenario autonomy mode (resets circuit breaker)."""
    clean_key = scenario_key.strip().lower()
    try:
        dto = AutopilotPolicyUpdateDTO(mode=req.mode)
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
    await policy_service.record_success(scenario_key=clean_key, session=db)
    return await policy_service.get_policy(scenario_key=clean_key, session=db)


@router.get("/scenarios/catalog", response_model=ScenarioCatalogResponse)
async def get_scenario_catalog(
    _auth: Optional[str] = Depends(get_intraservice_auth),
    service: AutopilotService = Depends(get_autopilot_service_dep),
    db: AsyncSession = Depends(get_db_session),
) -> ScenarioCatalogResponse:
    """Server-side catalog of executable scenarios available for operator correction."""
    catalog = await service.catalog_service.get_catalog(session=db)
    return ScenarioCatalogResponse(scenarios=catalog, total=len(catalog))


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
    stmt = select(func.count(CommandRecord.id)).where(CommandRecord.status == "succeeded")
    res = await db.execute(stmt)
    succeeded_count = res.scalar() or 0

    policies = await policy_service.list_policies(session=db)
    active_count = sum(1 for p in policies if p.mode != "DISABLED")
    tripped_count = sum(1 for p in policies if p.is_circuit_broken)

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
    """Strictly read-only plan query from cache or database for supervisor inspection."""
    return await service.get_agent_plan(
        ticket_id=ticket_id,
        session=db,
        redis_client=redis,
        auth_b64=auth_b64,
    )


@router.post("/plan/{ticket_id}/analyze", response_model=AgentPlanDTO)
async def analyze_ticket_plan(
    ticket_id: int,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> AgentPlanDTO:
    """Execute Evidence Routing Cascade analysis for a ticket if not already computed."""
    return await service.analyze_ticket_plan(
        ticket_id=ticket_id,
        force=False,
        auth_b64=auth_b64,
        session=db,
        redis_client=redis,
    )


@router.post("/plan/{ticket_id}/reanalyze", response_model=AgentPlanDTO)
async def reanalyze_ticket_plan(
    ticket_id: int,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> AgentPlanDTO:
    """Force re-run Evidence Routing Cascade analysis for a ticket with fresh snapshot."""
    return await service.analyze_ticket_plan(
        ticket_id=ticket_id,
        force=True,
        auth_b64=auth_b64,
        session=db,
        redis_client=redis,
    )


@router.post("/plan/{ticket_id}/approve", response_model=ApprovePlanResponse)
async def approve_agent_plan(
    ticket_id: int,
    req: ApprovePlanRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> ApprovePlanResponse:
    """1-Click approve agent plan: verify OCC and preflight, create CommandRecord, log positive feedback."""
    operator = _extract_username(auth_b64)
    return await service.approve_plan(
        ticket_id=ticket_id,
        req=req,
        operator_username=operator,
        session=db,
        redis_client=redis,
        auth_b64=auth_b64,
    )


@router.post("/plan/{ticket_id}/correct", response_model=CorrectPlanResponse)
async def correct_agent_plan(
    ticket_id: int,
    req: CorrectPlanRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> CorrectPlanResponse:
    """Correct agent plan: preflight validation, record RoutingFeedbackRecord and create CommandRecord."""
    operator = _extract_username(auth_b64)
    return await service.correct_plan(
        ticket_id=ticket_id,
        req=req,
        operator_username=operator,
        session=db,
        redis_client=redis,
        auth_b64=auth_b64,
    )


@router.post("/plan/{ticket_id}/reject", response_model=RejectPlanResponse)
async def reject_agent_plan(
    ticket_id: int,
    req: RejectPlanRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> RejectPlanResponse:
    """Reject proposal: log negative feedback without creating execution commands."""
    operator = _extract_username(auth_b64)
    return await service.reject_plan(
        ticket_id=ticket_id,
        req=req,
        operator_username=operator,
        session=db,
        redis_client=redis,
    )


@router.post("/plan/{ticket_id}/manual-takeover", response_model=ManualTakeoverResponse)
async def manual_takeover_agent_plan(
    ticket_id: int,
    req: ManualTakeoverRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> ManualTakeoverResponse:
    """Leave ticket for human engineer takeover without running automation."""
    operator = _extract_username(auth_b64)
    return await service.manual_takeover_plan(
        ticket_id=ticket_id,
        req=req,
        operator_username=operator,
        session=db,
        redis_client=redis,
    )


@router.get("/feedback", response_model=FeedbackListResponse)
async def list_feedback(
    task_id: Optional[int] = Query(default=None),
    verdict: Optional[str] = Query(default=None),
    scenario_key: Optional[str] = Query(default=None),
    reason_tag: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> FeedbackListResponse:
    """Retrieve historical supervisor feedback and correction records."""
    return await service.get_feedback_list(
        session=db,
        task_id=task_id,
        verdict=verdict,
        scenario_key=scenario_key,
        reason_tag=reason_tag,
        limit=limit,
        offset=offset,
    )


@router.get("/feedback/export")
async def export_feedback_dataset(
    scenario_key: Optional[str] = Query(default=None),
    min_samples: Optional[int] = Query(default=None, ge=1),
    format: str = Query(default="jsonl", pattern="^(jsonl|json)$"),
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> Response:
    """Export sanitized Ground-Truth feedback calibration dataset."""
    dataset = await service.export_feedback(session=db, scenario_key=scenario_key, min_samples=min_samples)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if format == "json":
        content = json.dumps(dataset, ensure_ascii=False, indent=2)
        media_type = "application/json; charset=utf-8"
        filename = f"routing_feedback_{timestamp}.json"
    else:
        lines = [json.dumps(row, ensure_ascii=False) for row in dataset]
        content = "\n".join(lines) + ("\n" if lines else "")
        media_type = "application/x-ndjson; charset=utf-8"
        filename = f"routing_feedback_{timestamp}.jsonl"

    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/metrics/quality", response_model=RoutingQualityMetricsResponse)
async def get_quality_metrics(
    scenario_key: Optional[str] = Query(default=None),
    router_version: Optional[str] = Query(default=None),
    prompt_version: Optional[str] = Query(default=None),
    routing_state: Optional[str] = Query(default=None),
    _auth: Optional[str] = Depends(get_intraservice_auth),
    db: AsyncSession = Depends(get_db_session),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> RoutingQualityMetricsResponse:
    """Retrieve aggregated routing quality, agreement and calibration metrics."""
    metrics = await service.get_quality_metrics(
        session=db,
        scenario_key=scenario_key,
        router_version=router_version,
        prompt_version=prompt_version,
        routing_state=routing_state,
    )
    return RoutingQualityMetricsResponse(metrics=metrics)


@router.post("/batch-assign", response_model=BatchAssignResponse)
async def batch_assign(
    req: BatchAssignRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> BatchAssignResponse:
    """Batch assign tickets to service bot and dispatch background autopilot tasks."""
    return await service.batch_assign(
        ticket_ids=req.ticket_ids,
        redis_client=redis,
        auth_b64=auth_b64,
    )


@router.post("/reclaim/{ticket_id}")
async def reclaim_ticket(
    ticket_id: int,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    redis: aioredis.Redis = Depends(get_redis),
    service: AutopilotService = Depends(get_autopilot_service_dep),
) -> dict:
    """Instantly reclaim ticket by human operator with cooperative worker cancellation."""
    operator = _extract_username(auth_b64)
    return await service.reclaim_ticket(
        ticket_id=ticket_id,
        operator_username=operator,
        redis_client=redis,
        auth_b64=auth_b64,
    )
