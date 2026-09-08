"""Web API for durable manual/autopilot ticket cycles."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database.db import AutopilotScenario, CommandRecord, TicketRun, TicketRunEvent, get_db
from app.routers.deps import get_service_auth_b64, require_permission, verify_trusted_origin
from app.services.identity import PrincipalContext
from app.services.intraservice import get_single_task
from app.services.ticket_runs import (
    TicketRunMode,
    TicketRunService,
    task_executor_ids,
)
from app.services.command_service import serialize_command

router = APIRouter(prefix="/api/v2/ticket-runs", tags=["Ticket runs"])
settings_router = APIRouter(prefix="/api/v2/autopilot", tags=["Autopilot settings"])


class StartRunRequest(BaseModel):
    mode: Literal["manual", "autopilot"]


class ControlRunRequest(BaseModel):
    action: Literal["pause", "resume", "switch_mode"]
    expected_version: int = Field(ge=1)
    mode: Literal["manual", "autopilot"] | None = None


class AutopilotSettingRequest(BaseModel):
    enabled: bool
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class AutopilotScenarioRequest(BaseModel):
    service_id: int = Field(gt=0)
    scenario_key: Literal[
        "printer_installation",
        "user_creation",
        "install_printer",
        "create_user",
        "offline_host",
        "grant_wlan",
        "redirect",
        "physical_device",
        "file_lock",
        "rag_consultation",
        "consultation",
    ]
    enabled: bool = False
    rollout_mode: Literal["legacy", "shadow", "canary", "active"] = "legacy"
    config: dict = Field(default_factory=dict)
    expected_version: int | None = Field(None, ge=1)


def serialize_scenario(item: AutopilotScenario) -> dict:
    return {
        "id": str(item.id),
        "service_id": item.service_id,
        "scenario_key": item.scenario_key,
        "enabled": item.enabled,
        "rollout_mode": item.rollout_mode,
        "version": item.version,
        "config": item.config_json,
        "updated_by": item.updated_by,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def serialize_run(run: TicketRun) -> dict:
    return {
        "id": str(run.id),
        "task_id": run.task_id,
        "mode": run.mode,
        "state": run.state,
        "outcome": run.outcome,
        "current_step": run.current_step,
        "scenario_key": run.scenario_key,
        "scenario_version": run.scenario_version,
        "fact_revision": run.fact_revision,
        "context_fingerprint": run.context_fingerprint,
        "decision_version": run.decision_version,
        "waiting_reason": run.waiting_reason,
        "waiting_until": run.waiting_until.isoformat() if run.waiting_until else None,
        "clarification_count": run.clarification_count,
        "pause_reason": run.pause_reason,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "version": run.version,
        "trigger_kind": run.trigger_kind,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def serialize_event(event: TicketRunEvent) -> dict:
    return {
        "id": str(event.id),
        "sequence": event.sequence,
        "event_key": event.event_key,
        "event_type": event.event_type,
        "actor": event.actor,
        "details": event.details_json,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def run_error(exc: ValueError) -> HTTPException:
    code = str(exc)
    if code == "run_not_found":
        return HTTPException(status.HTTP_404_NOT_FOUND, code)
    if code in {
        "run_completed",
        "run_version_conflict",
        "autopilot_disabled",
        "ticket_status_not_active",
    }:
        return HTTPException(status.HTTP_409_CONFLICT, code)
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, code)


@router.get("")
async def list_task_runs(
    task_ids: list[int] = Query(default=[]),
    _context: PrincipalContext = Depends(require_permission("autopilot:read")),
    db: AsyncSession = Depends(get_db),
):
    unique_task_ids = list(dict.fromkeys(task_ids))
    if len(unique_task_ids) > 200:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "too_many_task_ids")
    if not unique_task_ids:
        return {"items": []}
    records = list(
        (
            await db.scalars(
                select(TicketRun)
                .where(TicketRun.task_id.in_(unique_task_ids))
                .order_by(TicketRun.task_id, TicketRun.created_at.desc())
            )
        ).all()
    )
    latest_by_task: dict[int, TicketRun] = {}
    for run in records:
        latest_by_task.setdefault(run.task_id, run)
    return {"items": [serialize_run(run) for run in latest_by_task.values()]}


@router.get("/by-task/{task_id}")
async def get_task_run(
    task_id: int,
    _context: PrincipalContext = Depends(require_permission("autopilot:read")),
    db: AsyncSession = Depends(get_db),
):
    service = TicketRunService(db)
    run = await service.get_latest_for_task(task_id)
    if run is None:
        return {"run": None, "events": [], "pending_command": None}
    events = await service.get_events(run.id)
    pending_command = await db.scalar(
        select(CommandRecord)
        .where(
            CommandRecord.ticket_run_id == run.id,
            CommandRecord.status == "awaiting_approval",
        )
        .order_by(CommandRecord.created_at.desc())
        .limit(1)
    )
    return {
        "run": serialize_run(run),
        "events": [serialize_event(item) for item in events],
        "pending_command": serialize_command(pending_command) if pending_command else None,
    }


@router.post("/by-task/{task_id}", status_code=status.HTTP_201_CREATED)
async def start_task_run(
    task_id: int,
    payload: StartRunRequest,
    context: PrincipalContext = Depends(require_permission("autopilot:control")),
    _origin: None = Depends(verify_trusted_origin),
    service_auth_b64: str = Depends(get_service_auth_b64),
    db: AsyncSession = Depends(get_db),
):
    task = await get_single_task(service_auth_b64, task_id)
    if not isinstance(task, dict):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket_not_found")
    task_status = int(task.get("StatusId") or 0)
    service = TicketRunService(db)
    try:
        if payload.mode == TicketRunMode.MANUAL.value:
            run = await service.start_manual(
                task_id=task_id,
                status_id=task_status,
                allowed_status_ids={
                    settings.STATUS_OPEN_ID,
                    settings.STATUS_WAITING_ID,
                    settings.STATUS_IN_PROGRESS_ID,
                },
                actor=context.subject,
                trigger_key=f"explicit:{uuid.uuid4()}",
            )
        else:
            global_setting = await service.get_global_setting()
            if global_setting is None or not global_setting.enabled:
                raise ValueError("autopilot_disabled")
            from app.services.vault import get_service_account_user_id

            assistant_user_id = await get_service_account_user_id(db)
            if not assistant_user_id or assistant_user_id not in task_executor_ids(task):
                raise ValueError("assistant_not_assigned")
            result = await service.register_assignment(
                task=task,
                assistant_user_id=assistant_user_id,
                open_status_id=settings.STATUS_OPEN_ID,
                trigger_key=f"explicit:{uuid.uuid4()}",
                trigger_kind="explicit_start",
                actor=context.subject,
            )
            if result.run is None:
                raise ValueError(result.reason)
            run = result.run
    except ValueError as exc:
        raise run_error(exc) from exc
    return serialize_run(run)


@router.post("/{run_id}/control")
async def control_task_run(
    run_id: uuid.UUID,
    payload: ControlRunRequest,
    context: PrincipalContext = Depends(require_permission("autopilot:control")),
    _origin: None = Depends(verify_trusted_origin),
    service_auth_b64: str = Depends(get_service_auth_b64),
    db: AsyncSession = Depends(get_db),
):
    service = TicketRunService(db)
    run = await db.get(TicketRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run_not_found")
    target_mode = TicketRunMode(payload.mode) if payload.mode else None
    if payload.action == "switch_mode" and target_mode is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "mode_required")

    if payload.action in {"resume", "switch_mode"}:
        task = await get_single_task(service_auth_b64, run.task_id)
        if not isinstance(task, dict):
            raise HTTPException(status.HTTP_409_CONFLICT, "ticket_unavailable")
        task_status = int(task.get("StatusId") or 0)
        if task_status in {
            settings.STATUS_COMPLETED_ID,
            settings.STATUS_CANCELLED_ID,
            settings.STATUS_CLOSED_ID,
        }:
            raise HTTPException(status.HTTP_409_CONFLICT, "ticket_status_terminal")
        effective_mode = target_mode or TicketRunMode(run.mode)
        if effective_mode == TicketRunMode.AUTOPILOT:
            from app.services.vault import get_service_account_user_id

            assistant_user_id = await get_service_account_user_id(db)
            if not assistant_user_id or assistant_user_id not in task_executor_ids(task):
                raise HTTPException(status.HTTP_409_CONFLICT, "assistant_not_assigned")
            service_id = int(task.get("ServiceId") or 0)
            if await service.get_enabled_scenario(service_id) is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "unsupported_service")

    try:
        updated = await service.update_control(
            run_id=run_id,
            action=payload.action,
            actor=context.subject,
            expected_version=payload.expected_version,
            target_mode=target_mode,
        )
    except ValueError as exc:
        raise run_error(exc) from exc
    return serialize_run(updated)


@settings_router.get("")
async def get_autopilot_setting(
    _context: PrincipalContext = Depends(require_permission("autopilot:read")),
    db: AsyncSession = Depends(get_db),
):
    setting = await TicketRunService(db).get_global_setting()
    assert setting is not None
    readiness = await TicketRunService(db).template_readiness()
    from app.services.vault import get_service_account_user_id

    service_user_id = await get_service_account_user_id(db)
    scenarios = await TicketRunService(db).list_scenarios()
    await db.commit()
    return {
        "enabled": setting.enabled,
        "version": setting.version,
        "updated_by": setting.updated_by,
        "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
        "templates_ready": readiness["ready"],
        "missing_templates": readiness["missing"],
        "service_user_id": service_user_id,
        "service_identity_ready": service_user_id is not None,
        "scenarios": [serialize_scenario(item) for item in scenarios],
    }


@settings_router.put("")
async def update_autopilot_setting(
    payload: AutopilotSettingRequest,
    context: PrincipalContext = Depends(require_permission("autopilot:manage")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.vault import get_service_account_user_id

    try:
        setting = await TicketRunService(db).set_global_enabled(
            enabled=payload.enabled,
            actor=context.subject,
            reason=payload.reason,
            expected_version=payload.expected_version,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {
        "enabled": setting.enabled,
        "version": setting.version,
        "updated_by": setting.updated_by,
        "updated_at": setting.updated_at.isoformat() if setting.updated_at else None,
        "templates_ready": True,
        "missing_templates": [],
        "service_user_id": await get_service_account_user_id(db),
        "scenarios": [serialize_scenario(item) for item in await TicketRunService(db).list_scenarios()],
    }


@settings_router.get("/scenarios")
async def list_autopilot_scenarios(
    _context: PrincipalContext = Depends(require_permission("autopilot:read")),
    db: AsyncSession = Depends(get_db),
):
    return {"items": [serialize_scenario(item) for item in await TicketRunService(db).list_scenarios()]}


@settings_router.put("/scenarios")
async def upsert_autopilot_scenario(
    payload: AutopilotScenarioRequest,
    context: PrincipalContext = Depends(require_permission("autopilot:manage")),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await TicketRunService(db).upsert_scenario(
            service_id=payload.service_id,
            scenario_key=payload.scenario_key,
            enabled=payload.enabled,
            rollout_mode=payload.rollout_mode,
            config=payload.config,
            actor=context.subject,
            expected_version=payload.expected_version,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return serialize_scenario(item)
