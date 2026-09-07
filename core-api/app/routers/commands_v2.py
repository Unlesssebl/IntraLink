"""Version 2 command API backed exclusively by PostgreSQL state."""

import asyncio
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import (
    ActionPolicyRecord,
    AsyncSessionLocal,
    CommandEvent,
    CommandRecord,
    get_db,
)
from app.routers.deps import (
    require_permission,
    require_object_permission,
    require_service_scope,
    verify_trusted_origin,
)
from app.services.command_service import CommandService, serialize_command
from app.services.identity import PrincipalContext, require_context_permission
from app.services.identity import create_approval_challenge, consume_approval_challenge
from app.services.worker import get_redis_client

router = APIRouter(prefix="/api/v2/commands", tags=["Commands v2"])
policy_router = APIRouter(prefix="/api/v2/action-policies", tags=["Action policies v2"])
workers_router = APIRouter(prefix="/api/v2/workers", tags=["Workers v2"])


class CreateCommandRequest(BaseModel):
    action: str
    target: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(5, ge=1, le=10)
    source: str = Field("api", max_length=32)
    ticket_run_id: uuid.UUID | None = None
    decision_id: uuid.UUID | None = None
    decision_version: int | None = Field(None, ge=1)


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None
    expected_plan_hash: str | None = None
    expected_version: int | None = Field(None, ge=1)


class PreflightReportRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    claim_token: str = Field(min_length=20)
    evidence: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(7200, ge=60, le=86400)


class PhaseReportRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    claim_token: str = Field(min_length=20)
    phase: str = Field(min_length=2, max_length=50)
    details: dict[str, Any] = Field(default_factory=dict)


class TelegramApprovalRequest(ApprovalRequest):
    challenge_token: str = Field(min_length=20, max_length=200)


class TelegramChallengeRequest(BaseModel):
    tg_user_id: int


class ClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    lease_seconds: int = Field(120, ge=30, le=900)
    message_id: str = Field(min_length=3, max_length=100)
    outbox_id: uuid.UUID | None = None


class FinishRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    claim_token: str = Field(min_length=20)
    outcome: Literal["succeeded", "failed", "needs_review"]
    result: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class HeartbeatRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    claim_token: str = Field(min_length=20)
    lease_seconds: int = Field(120, ge=30, le=900)


class QuarantineRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    missing_capability: str = Field(min_length=1, max_length=100)
    message_id: str = Field(min_length=3, max_length=100)
    reason: str | None = None



class ReviewRequest(BaseModel):
    decision: Literal["succeeded", "failed", "requeue"]
    reason: str = Field(min_length=3, max_length=1000)


class PolicyRequest(BaseModel):
    mode: Literal["auto", "confirm", "disabled"]
    reason: str = Field(min_length=3, max_length=1000)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_command(
    payload: CreateCommandRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=128),
    context: PrincipalContext = Depends(require_permission("command:create")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    command, duplicate = await CommandService(db).create(
        action=payload.action,
        target=payload.target,
        parameters=payload.parameters,
        idempotency_key=idempotency_key,
        initiator=context.subject,
        initiator_principal_id=context.principal_id,
        source=payload.source,
        priority=payload.priority,
        ticket_run_id=payload.ticket_run_id,
        decision_id=payload.decision_id,
        decision_version=payload.decision_version,
    )
    return {**serialize_command(command), "duplicate": duplicate}


@router.get("")
async def list_commands(
    status_filter: str | None = Query(None, alias="status"),
    action: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _context: PrincipalContext = Depends(require_permission("command:read")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(CommandRecord)
    if status_filter:
        stmt = stmt.where(CommandRecord.status == status_filter)
    if action:
        stmt = stmt.where(CommandRecord.action == action)
    records = list((await db.scalars(
        stmt.order_by(desc(CommandRecord.created_at)).limit(limit).offset(offset)
    )).all())
    return {"items": [serialize_command(item) for item in records], "limit": limit, "offset": offset}


@router.get("/{command_id}")
async def get_command(
    command_id: uuid.UUID,
    _context: PrincipalContext = Depends(require_permission("command:read")),
    db: AsyncSession = Depends(get_db),
):
    return serialize_command(await CommandService(db).get(command_id))


@router.post("/{command_id}/approval")
async def approve_command(
    command_id: uuid.UUID,
    payload: ApprovalRequest,
    context: PrincipalContext = Depends(require_object_permission("command:approve:dynamic")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    command = await CommandService(db).approve(
        command_id,
        decision=payload.decision,
        reason=payload.reason,
        operator=context.subject,
        expected_plan_hash=payload.expected_plan_hash,
        expected_version=payload.expected_version,
        approver_principal_id=context.principal_id,
        approver_roles=context.roles,
        approver_permissions=context.permissions,
    )
    return serialize_command(command)


@router.post("/{command_id}/approval/telegram")
async def approve_command_from_telegram(
    command_id: uuid.UUID,
    payload: TelegramApprovalRequest,
    _service: PrincipalContext = Depends(require_service_scope("telegram:challenge:consume")),
    db: AsyncSession = Depends(get_db),
):
    """Consume a short-lived, command-bound approval challenge."""
    current = await CommandService(db).get(command_id)
    approver = await consume_approval_challenge(
        db,
        command_id=command_id,
        request_hash=current.request_hash,
        token=payload.challenge_token,
        decision=payload.decision,
    )
    command = await CommandService(db).approve(
        command_id,
        decision=payload.decision,
        reason=payload.reason,
        operator=f"telegram:{approver.subject}",
        expected_plan_hash=payload.expected_plan_hash,
        expected_version=payload.expected_version,
        approver_principal_id=approver.principal_id,
        approver_roles=approver.roles,
        approver_permissions=approver.permissions,
    )
    return serialize_command(command)


@router.post("/{command_id}/approval/telegram/challenge")
async def issue_telegram_approval_challenge(
    command_id: uuid.UUID,
    payload: TelegramChallengeRequest,
    _service: PrincipalContext = Depends(require_service_scope("telegram:challenge:issue")),
    db: AsyncSession = Depends(get_db),
):
    command = await CommandService(db).get(command_id)
    if command.status != "awaiting_approval":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Command is {command.status}")
    challenge = await create_approval_challenge(
        db,
        command_id=command.id,
        request_hash=command.request_hash,
        tg_user_id=payload.tg_user_id,
    )
    return {"challenge_token": challenge, "expires_in": 600}


@router.post("/{command_id}/cancel")
async def cancel_command(
    command_id: uuid.UUID,
    reason: str = Query(..., min_length=3, max_length=500),
    context: PrincipalContext = Depends(require_permission("command:cancel")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    return serialize_command(
        await CommandService(db).cancel(command_id, reason=reason, actor=context.subject)
    )


@router.post("/{command_id}/claim")
async def claim_command(
    command_id: uuid.UUID,
    payload: ClaimRequest,
    context: PrincipalContext = Depends(require_object_permission("command:claim:executor")),
    db: AsyncSession = Depends(get_db),
):
    command = await CommandService(db).get(command_id)
    if context.principal_type != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service identity required")
    require_context_permission(context, f"command:claim:{command.executor}")
    claim = await CommandService(db).claim(
        command_id,
        worker_id=payload.worker_id,
        lease_seconds=payload.lease_seconds,
        message_id=payload.message_id,
        outbox_id=payload.outbox_id,
    )
    return {
        **serialize_command(claim.command),
        "claim_token": claim.token,
        "attempt_no": claim.attempt_no,
    }


@router.post("/{command_id}/finish")
async def finish_command(
    command_id: uuid.UUID,
    payload: FinishRequest,
    context: PrincipalContext = Depends(require_object_permission("command:finish:executor")),
    db: AsyncSession = Depends(get_db),
):
    current = await CommandService(db).get(command_id)
    if context.principal_type != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service identity required")
    require_context_permission(context, f"command:finish:{current.executor}")
    command = await CommandService(db).finish(
        command_id,
        claim_token=payload.claim_token,
        outcome=payload.outcome,
        result=payload.result,
        error_message=payload.error_message,
        worker_id=payload.worker_id,
    )
    return serialize_command(command)
 
 
@router.post("/{command_id}/heartbeat")
async def heartbeat_command(
    command_id: uuid.UUID,
    payload: HeartbeatRequest,
    context: PrincipalContext = Depends(require_object_permission("command:heartbeat:executor")),
    db: AsyncSession = Depends(get_db),
):
    current = await CommandService(db).get(command_id)
    if context.principal_type != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service identity required")
    require_context_permission(context, f"command:claim:{current.executor}")
    command, lease_ttl_seconds = await CommandService(db).renew_lease(
        command_id,
        worker_id=payload.worker_id,
        claim_token=payload.claim_token,
        lease_seconds=payload.lease_seconds,
    )
    return {
        "command_id": str(command.id),
        "status": command.status,
        "lease_ttl_seconds": lease_ttl_seconds,
        "lease_expires_at": command.lease_expires_at.isoformat() if command.lease_expires_at else None,
    }


@router.post("/{command_id}/preflight")
async def record_command_preflight(
    command_id: uuid.UUID,
    payload: PreflightReportRequest,
    context: PrincipalContext = Depends(require_object_permission("command:claim:executor")),
    db: AsyncSession = Depends(get_db),
):
    current = await CommandService(db).get(command_id)
    if context.principal_type != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service identity required")
    require_context_permission(context, f"command:claim:{current.executor}")
    command = await CommandService(db).record_preflight(
        command_id,
        worker_id=payload.worker_id,
        claim_token=payload.claim_token,
        evidence=payload.evidence,
        ttl_seconds=payload.ttl_seconds,
    )
    return serialize_command(command)


@router.post("/{command_id}/phase")
async def record_command_phase(
    command_id: uuid.UUID,
    payload: PhaseReportRequest,
    context: PrincipalContext = Depends(require_object_permission("command:claim:executor")),
    db: AsyncSession = Depends(get_db),
):
    current = await CommandService(db).get(command_id)
    if context.principal_type != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service identity required")
    require_context_permission(context, f"command:claim:{current.executor}")
    command = await CommandService(db).record_phase(
        command_id,
        worker_id=payload.worker_id,
        claim_token=payload.claim_token,
        phase=payload.phase,
        details=payload.details,
    )
    return serialize_command(command)


@router.post("/{command_id}/quarantine")
async def quarantine_command(
    command_id: uuid.UUID,
    payload: QuarantineRequest,
    context: PrincipalContext = Depends(require_object_permission("command:claim:executor")),
    db: AsyncSession = Depends(get_db),
):
    current = await CommandService(db).get(command_id)
    if context.principal_type != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service identity required")
    require_context_permission(context, f"command:claim:{current.executor}")
    command = await CommandService(db).quarantine(
        command_id,
        worker_id=payload.worker_id,
        missing_capability=payload.missing_capability,
        message_id=payload.message_id,
        reason=payload.reason,
    )
    return {
        **serialize_command(command),
        "quarantined": True,
        "missing_capability": payload.missing_capability,
    }


@router.post("/{command_id}/review")
async def resolve_command_review(
    command_id: uuid.UUID,
    payload: ReviewRequest,
    context: PrincipalContext = Depends(require_permission("command:review")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    command = await CommandService(db).resolve_review(
        command_id,
        decision=payload.decision,
        reason=payload.reason,
        actor=context.subject,
    )
    return serialize_command(command)


@router.get("/{command_id}/events")
async def stream_command_events(
    command_id: uuid.UUID,
    last_event_id: int | None = Header(None, alias="Last-Event-ID"),
    _context: PrincipalContext = Depends(require_permission("command:read")),
):
    async def generate():
        cursor = int(last_event_id or 0)
        idle_rounds = 0
        while idle_rounds < 300:
            async with AsyncSessionLocal() as db:
                events = list((await db.scalars(
                    select(CommandEvent)
                    .where(
                        CommandEvent.command_id == command_id,
                        CommandEvent.sequence > cursor,
                    )
                    .order_by(CommandEvent.sequence)
                )).all())
                command = await db.get(CommandRecord, command_id)
            for event in events:
                cursor = event.sequence
                idle_rounds = 0
                payload = {
                    "type": event.event_type,
                    "details": event.details_json,
                    "actor": event.actor,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                }
                yield f"id: {cursor}\nevent: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if command is None or (command.status in {"succeeded", "failed", "rejected", "cancelled", "needs_review"} and not events):
                return
            idle_rounds += 1
            if not events:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream")


@policy_router.get("")
async def list_action_policies(
    _context: PrincipalContext = Depends(require_permission("command:read")),
    db: AsyncSession = Depends(get_db),
):
    records = list((await db.scalars(select(ActionPolicyRecord).order_by(ActionPolicyRecord.action))).all())
    return {
        "items": [
            {
                "action": item.action,
                "mode": item.mode,
                "updated_by": item.updated_by,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in records
        ]
    }


@policy_router.put("/{action}")
async def update_action_policy(
    action: str,
    payload: PolicyRequest,
    context: PrincipalContext = Depends(require_permission("policy:manage")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    item = await CommandService(db).set_policy(
        action, mode=payload.mode, actor=context.subject, reason=payload.reason,
        principal_id=context.principal_id,
    )
    return {"action": item.action, "mode": item.mode, "updated_by": item.updated_by}


@workers_router.get("/fleet")
async def get_worker_fleet(
    _context: PrincipalContext = Depends(require_permission("command:read")),
):
    """Возвращает список зарегистрированных узлов Worker Fleet из Redis."""
    redis = get_redis_client()
    node_ids = await redis.smembers("worker:nodes")
    nodes: list[dict[str, Any]] = []
    stale_nodes: list[str] = []

    for raw_node in node_ids:
        node_id = raw_node.decode("utf-8") if isinstance(raw_node, bytes) else str(raw_node)
        card_raw = await redis.get(f"worker:node:{node_id}")
        if not card_raw:
            stale_nodes.append(node_id)
            continue
        try:
            card = json.loads(card_raw)
            nodes.append(card)
        except Exception:
            stale_nodes.append(node_id)

    if stale_nodes:
        await redis.srem("worker:nodes", *stale_nodes)

    return {
        "nodes": sorted(nodes, key=lambda n: str(n.get("node_id", ""))),
        "total_active": len(nodes),
    }


@workers_router.get("/readiness")
async def get_worker_readiness():
    """Проверяет доступность флота воркеров и критических возможностей."""
    redis = get_redis_client()
    node_ids = await redis.smembers("worker:nodes")
    available_capabilities: set[str] = set()
    active_count = 0
    stale_nodes: list[str] = []

    for raw_node in node_ids:
        node_id = raw_node.decode("utf-8") if isinstance(raw_node, bytes) else str(raw_node)
        card_raw = await redis.get(f"worker:node:{node_id}")
        if not card_raw:
            stale_nodes.append(node_id)
            continue
        try:
            card = json.loads(card_raw)
            active_count += 1
            for cap in card.get("capabilities", []):
                available_capabilities.add(cap)
        except Exception:
            stale_nodes.append(node_id)

    if stale_nodes:
        await redis.srem("worker:nodes", *stale_nodes)

    critical_capabilities = {"windows", "printers", "winrm"}
    missing_critical = sorted(list(critical_capabilities - available_capabilities))
    is_ready = (active_count > 0) and (len(missing_critical) == 0)

    return {
        "ready": is_ready,
        "active_nodes_count": active_count,
        "available_capabilities": sorted(list(available_capabilities)),
        "missing_critical_capabilities": missing_critical,
    }
