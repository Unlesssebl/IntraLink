"""Version 2 command API backed exclusively by PostgreSQL state."""

import asyncio
import datetime
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
    TicketRun,
    get_db,
)
from app.routers.deps import (
    require_permission,
    require_object_permission,
    require_service_scope,
    verify_trusted_origin,
)
from app.services import intraservice
from app.services.command_delivery import CommandDeliveryService
from app.services.command_secrets import CommandSecretService
from app.services.command_service import CommandService, serialize_command
from app.services.decision_journal import (
    DecisionJournalService,
    ticket_snapshot_fingerprint,
)
from app.services.identity import PrincipalContext, require_context_permission
from app.services.vault import get_service_account_auth_b64
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
    source: Literal["api", "web", "autopilot", "triage", "assistant"] = "api"
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


class SecretArtifactRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    claim_token: str = Field(min_length=20)
    name: Literal["temporary_password"]
    value: str = Field(min_length=1, max_length=1024)
    ttl_seconds: int = Field(900, ge=60, le=3600)


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
    task_id_raw = payload.target.get("task_id") or payload.parameters.get("task_id")
    try:
        task_id = int(task_id_raw) if task_id_raw is not None else None
    except (TypeError, ValueError):
        task_id = None

    decision_id = payload.decision_id
    decision_version = payload.decision_version
    if task_id is not None:
        service_auth_b64 = await get_service_account_auth_b64(db)
        if not service_auth_b64:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "service_account_not_configured",
            )
        current_task = await intraservice.get_single_task(service_auth_b64, task_id)
        current_history = await intraservice.get_task_lifetime(service_auth_b64, task_id)
        if current_task is None or current_history is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ticket_context_unavailable",
            )
        journal = DecisionJournalService(db)
        if decision_id is None:
            generated = await journal.record_operational(
                task_id=task_id,
                ticket_run_id=payload.ticket_run_id,
                action=payload.action,
                target=payload.target,
                parameters=payload.parameters,
                actor=context.subject,
                task=current_task,
                history=current_history,
            )
            decision_id = generated.id
            decision_version = generated.version
        else:
            if decision_version is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "decision_version_and_task_id_required",
                )
            decision = await journal.require_current(
                decision_id=decision_id,
                task_id=task_id,
                version=decision_version,
            )
            expected_fingerprint = decision.context_json.get("ticket_fingerprint")
            if not expected_fingerprint:
                raise HTTPException(status.HTTP_409_CONFLICT, "decision_context_unverifiable")
            if expected_fingerprint != ticket_snapshot_fingerprint(current_task, current_history):
                raise HTTPException(status.HTTP_409_CONFLICT, "decision_stale")

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
        decision_id=decision_id,
        decision_version=decision_version,
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


@router.post("/{command_id}/secret-artifacts", status_code=status.HTTP_201_CREATED)
async def store_secret_artifact(
    command_id: uuid.UUID,
    payload: SecretArtifactRequest,
    context: PrincipalContext = Depends(require_object_permission("command:finish:executor")),
    db: AsyncSession = Depends(get_db),
):
    current = await CommandService(db).get(command_id)
    if context.principal_type != "service":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Service identity required")
    require_context_permission(context, f"command:finish:{current.executor}")
    artifact = await CommandSecretService(db).store(
        command_id,
        claim_token=payload.claim_token,
        name=payload.name,
        value=payload.value,
        ttl_seconds=payload.ttl_seconds,
    )
    return {
        "id": str(artifact.id),
        "name": artifact.name,
        "expires_at": artifact.expires_at.isoformat(),
    }


@router.post("/{command_id}/deliver")
async def deliver_command(
    command_id: uuid.UUID,
    context: PrincipalContext = Depends(require_permission("command:create")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    """Deliver the verified command resolution to the external ticketing system."""
    return await CommandDeliveryService(db).deliver_create_user(
        command_id,
        actor=context.subject,
    )


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


ACTION_TITLES: dict[str, str] = {
    "install_printer": "Установка принтера",
    "wlan_access": "Предоставление доступа к Wi-Fi",
    "printer_diagnostics": "Диагностика принтера",
    "host_diagnostics": "Диагностика рабочей станции",
}

PHASE_TITLES: dict[str, str] = {
    "validate": "Валидация параметров задачи",
    "preflight": "Проверка доступности ПК и сетевых портов",
    "prepare": "Подготовка окружения и драйверов",
    "execute": "Установка и настройка оборудования",
    "verify": "Верификация установленного принтера",
    "reconcile": "Сверка и устранение расхождений",
    "cleanup": "Очистка временных файлов",
    "driver_copy": "Копирование драйвера печати",
    "printer_port": "Создание TCP/IP порта печати (RAW 9100)",
    "printer_queue": "Регистрация очереди печати Windows",
    "diagnostics": "Сетевая экспресс-диагностика",
    "ad_execution": "Добавление в доменную группу AD",
    "closing_ticket": "Формирование отчёта и закрытие заявки",
    "searching_user": "Поиск учётной записи в Active Directory",
    "host_ping": "Проверка сетевого отклика хоста",
    "installing": "Установка компонентов",
}


@workers_router.get("/active-execution")
async def get_active_worker_execution(
    _context: PrincipalContext = Depends(require_permission("command:read")),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает текущую выполняемую ассистентом/воркером заявку и понятный человекочитаемый статус."""
    worker_online = False
    active_nodes_count = 0
    try:
        redis = get_redis_client()
        node_ids = await redis.smembers("worker:nodes")
        worker_online = len(node_ids) > 0
        active_nodes_count = len(node_ids)
    except Exception:
        pass

    # 1. Проверяем активную команду в commands
    cmd_stmt = (
        select(CommandRecord)
        .where(CommandRecord.status.in_(["running", "awaiting_approval", "queued"]))
        .order_by(desc(CommandRecord.updated_at))
        .limit(1)
    )
    active_cmd = (await db.scalars(cmd_stmt)).first()

    if active_cmd is not None:
        evt_stmt = (
            select(CommandEvent)
            .where(
                CommandEvent.command_id == active_cmd.id,
                CommandEvent.event_type.like("phase_%"),
            )
            .order_by(desc(CommandEvent.sequence))
            .limit(1)
        )
        last_phase_evt = (await db.scalars(evt_stmt)).first()

        ticket_run = None
        if active_cmd.ticket_run_id:
            ticket_run = await db.get(TicketRun, active_cmd.ticket_run_id)
        elif active_cmd.task_id:
            ticket_run = (
                await db.scalars(
                    select(TicketRun)
                    .where(TicketRun.task_id == active_cmd.task_id)
                    .order_by(desc(TicketRun.created_at))
                    .limit(1)
                )
            ).first()

        target = active_cmd.target_json or {}
        target_host = target.get("pc_name") or target.get("host") or target.get("ip") or ""
        params = active_cmd.params_json or {}
        target_printer = params.get("printer_name") or params.get("model") or ""

        action_title = ACTION_TITLES.get(active_cmd.action, active_cmd.action)
        phase_raw = ""
        progress_pct = None
        phase_detail = ""

        if last_phase_evt:
            evt_details = last_phase_evt.details_json or {}
            phase_raw = evt_details.get("phase") or last_phase_evt.event_type.removeprefix("phase_")
            details_inner = evt_details.get("details") or {}
            progress_pct = details_inner.get("pct") or details_inner.get("progress")
            phase_detail = details_inner.get("detail") or details_inner.get("step") or ""

        phase_title = phase_detail or PHASE_TITLES.get(
            phase_raw, phase_raw.replace("_", " ").capitalize() if phase_raw else "Выполнение"
        )
        if progress_pct is not None:
            phase_display = f"{phase_title} ({progress_pct}%)"
        else:
            phase_display = phase_title

        target_display = f" на {target_host}" if target_host else ""
        printer_display = f" {target_printer}" if target_printer else ""

        if active_cmd.status == "awaiting_approval":
            status_text = f"Ожидает подтверждения: {action_title}{printer_display}{target_display}".strip()
            state = "waiting_approval"
        elif active_cmd.status == "queued":
            status_text = f"В очереди исполнения: {action_title}{printer_display}{target_display}".strip()
            state = "queued"
        else:
            status_text = f"{action_title}{printer_display}{target_display} — {phase_display}".strip()
            state = "running"

        return {
            "has_active": True,
            "state": state,
            "mode": ticket_run.mode if ticket_run else "manual",
            "task_id": active_cmd.task_id,
            "command_id": str(active_cmd.id),
            "ticket_run_id": str(active_cmd.ticket_run_id) if active_cmd.ticket_run_id else None,
            "action": active_cmd.action,
            "action_title": action_title,
            "target_host": target_host or None,
            "target_printer": target_printer or None,
            "phase": phase_raw or None,
            "phase_title": phase_title,
            "progress_pct": progress_pct,
            "status_text": status_text,
            "worker_online": worker_online,
            "active_nodes_count": active_nodes_count,
            "updated_at": active_cmd.updated_at.isoformat() if active_cmd.updated_at else None,
        }

    # 2. Проверяем активный TicketRun без запущенной команды
    run_stmt = (
        select(TicketRun)
        .where(
            TicketRun.completed_at.is_(None),
            TicketRun.state.in_(["running", "waiting_approval", "waiting_answer", "pending", "paused"]),
        )
        .order_by(desc(TicketRun.updated_at))
        .limit(1)
    )
    active_run = (await db.scalars(run_stmt)).first()

    if active_run is not None:
        step_title = active_run.current_step or "Анализ заявки"
        if active_run.state == "waiting_approval":
            status_text = f"Заявка #{active_run.task_id}: Ожидает подтверждения оператора"
        elif active_run.state == "waiting_answer":
            status_text = f"Заявка #{active_run.task_id}: Ожидание ответа заявителя"
        elif active_run.state == "paused":
            status_text = f"Заявка #{active_run.task_id}: Приостановлена оператором"
        else:
            status_text = f"Заявка #{active_run.task_id}: {step_title}"

        return {
            "has_active": True,
            "state": active_run.state,
            "mode": active_run.mode,
            "task_id": active_run.task_id,
            "command_id": None,
            "ticket_run_id": str(active_run.id),
            "action": "autopilot_cycle",
            "action_title": "Автопилот",
            "target_host": None,
            "target_printer": None,
            "phase": active_run.current_step,
            "phase_title": step_title,
            "progress_pct": None,
            "status_text": status_text,
            "worker_online": worker_online,
            "active_nodes_count": active_nodes_count,
            "updated_at": active_run.updated_at.isoformat() if active_run.updated_at else None,
        }

    # 3. Никаких активных задач нет
    return {
        "has_active": False,
        "state": "idle",
        "mode": None,
        "task_id": None,
        "command_id": None,
        "ticket_run_id": None,
        "action": None,
        "action_title": None,
        "target_host": None,
        "target_printer": None,
        "phase": None,
        "phase_title": None,
        "progress_pct": None,
        "status_text": "Ассистент свободен (готов к приёму задач)" if worker_online else "Воркер не подключен",
        "worker_online": worker_online,
        "active_nodes_count": active_nodes_count,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
