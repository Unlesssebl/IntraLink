"""Безопасный bridge между web-панелью и локальным Desktop Companion."""

import datetime as dt
import hashlib
import ipaddress
import re
import secrets
import uuid
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import DesktopLaunchLog, get_db
from app.routers.deps import get_operator_context, require_permission
from app.services import intraservice
from app.services.host_telemetry import extract_pc_from_task
from shared.normalizer import normalize_pc_name

router = APIRouter(prefix="/api/v1/desktop", tags=["Desktop Companion"])
TOKEN_TTL_SECONDS = 60
HOST_INPUT_RE = re.compile(r"^[A-Za-zА-Яа-я0-9.-]{1,255}$")


class DesktopClient(StrEnum):
    LITEMANAGER = "litemanager"
    DAMEWARE = "dameware"
    RDP = "rdp"


class CreateLaunchRequest(BaseModel):
    task_id: int = Field(gt=0)
    host: str = Field(min_length=1, max_length=255)
    client: DesktopClient


class ClaimLaunchRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class ReportLaunchRequest(BaseModel):
    launch_id: uuid.UUID
    completion_token: str = Field(min_length=32, max_length=256)
    status: str = Field(pattern="^(launched|failed)$")
    error_message: str | None = Field(default=None, max_length=500)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clean_host(raw_host: str) -> str:
    raw_host = raw_host.strip()
    # Нормализатор умеет извлекать имя ПК из свободного текста. Для запуска
    # приложения это опасно: здесь допустим только один явный host/IP.
    if not HOST_INPUT_RE.fullmatch(raw_host):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Некорректное имя хоста")
    try:
        return str(ipaddress.ip_address(raw_host))
    except ValueError:
        pass
    host = normalize_pc_name(raw_host)
    if not host:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Некорректное имя хоста")
    return host


@router.post("/launches", dependencies=[Depends(require_permission("diagnostic:run"))])
async def create_launch(
    payload: CreateLaunchRequest,
    db: AsyncSession = Depends(get_db),
    operator=Depends(get_operator_context),
):
    """Создаёт короткоживущее одноразовое разрешение на запуск локального клиента."""
    host = _clean_host(payload.host)
    task = await intraservice.get_single_task(operator.auth_b64, payload.task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Заявка не найдена или недоступна")

    task_host = extract_pc_from_task(task)
    if not task_host or _clean_host(task_host) != host:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Хост не соответствует заявке")

    opaque_token = secrets.token_urlsafe(32)
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=TOKEN_TTL_SECONDS)
    log = DesktopLaunchLog(
        token_hash=_digest(opaque_token), task_id=payload.task_id, host=host,
        client=payload.client.value, initiator=operator.username, status="issued", expires_at=expires_at,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return {"deep_link": f"intralink://launch?token={opaque_token}", "expires_in": TOKEN_TTL_SECONDS, "launch_id": str(log.id)}


@router.post("/launches/claim")
async def claim_launch(payload: ClaimLaunchRequest, db: AsyncSession = Depends(get_db)):
    """Потребляется только Desktop Companion; token нельзя использовать повторно."""
    result = await db.execute(select(DesktopLaunchLog).where(DesktopLaunchLog.token_hash == _digest(payload.token)))
    log = result.scalar_one_or_none()
    now = dt.datetime.now(dt.UTC)
    if not log or log.status != "issued" or log.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Разрешение недействительно, истекло или уже использовано")

    completion_token = secrets.token_urlsafe(32)
    log.completion_hash = _digest(completion_token)
    log.status = "claimed"
    log.claimed_at = now
    await db.commit()
    return {"launch_id": str(log.id), "host": log.host, "client": log.client, "completion_token": completion_token}


@router.post("/launches/result")
async def report_launch(payload: ReportLaunchRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DesktopLaunchLog).where(DesktopLaunchLog.id == payload.launch_id))
    log = result.scalar_one_or_none()
    if not log or log.status != "claimed" or not log.completion_hash or not secrets.compare_digest(log.completion_hash, _digest(payload.completion_token)):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Отчёт запуска недействителен")
    log.status = payload.status
    log.error_message = payload.error_message if payload.status == "failed" else None
    log.completed_at = dt.datetime.now(dt.UTC)
    await db.commit()
    return {"status": log.status}
