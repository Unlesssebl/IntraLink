"""
Роутер Command Bus: Единая шина приёма и управления задачами исполнения (Command Bus / Execution Hub).
Обеспечивает валидацию, идемпотентность, персистентный аудит в PostgreSQL (job_log),
постановку в Redis Streams и поддержку протокола Human-in-the-Loop (HITL).
"""

import datetime
import json
import logging
import time
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import JobLog, get_db
from app.config import settings
from app.routers.deps import principal_subject, require_permission
from app.services.actions import get_policy_engine
from app.services.ai_suggestions import require_current_suggestion
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.routers.commands")

router = APIRouter(
    prefix="/api/v1/commands",
    tags=["Command Bus (Unified Execution Hub)"],
    dependencies=[Depends(require_permission("command:read"))],
)

STREAM_EXECUTION_QUEUE = "stream:execution_queue"


async def require_legacy_command_api() -> None:
    if settings.APP_ENV == "production":
        raise HTTPException(
            status.HTTP_410_GONE,
            "Legacy command API is disabled; use /api/v2/commands",
        )


class SubmitCommandRequest(BaseModel):
    type: str = Field(
        ...,
        description="Тип команды: grant_wlan, create_user, diagnose_host, install_printer, apply_triage, rag_sync, etc.",
    )
    target: dict[str, Any] = Field(
        default_factory=dict,
        description="Целевой объект: task_id, host, identity, login и др.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="Дополнительные параметры выполнения"
    )
    mode: str = Field(
        "auto",
        description="Режим выполнения: auto (автономно) | confirm (HITL с подтверждением) | dry_run (симуляция)",
    )
    priority: int = Field(5, ge=1, le=10, description="Приоритет команды от 1 до 10")
    idempotency_key: str | None = Field(
        None, description="Ключ идемпотентности для предотвращения дублей"
    )
    initiator: str | None = Field(
        None, description="Идентификатор оператора или системы"
    )
    source: str | None = Field(
        None, description="Источник вызова: cli | web | bot | cron"
    )
    auto_close_ticket: bool = Field(
        True, description="Автоматически финализировать заявку в IntraService при успехе"
    )
    suggestion_task_id: int | None = Field(
        None, description="ID заявки, для которой используется AI-предложение"
    )
    suggestion_fingerprint: str | None = Field(
        None, description="Версия актуального AI-предложения"
    )


class ConfirmDecisionRequest(BaseModel):
    decision: str = Field(
        ..., description="Решение оператора: 'approve' (одобрить) или 'reject' (отклонить)"
    )
    reason: str | None = Field(None, description="Причина решения или комментарий")
    operator: str | None = Field(None, description="Идентификатор оператора")


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_permission("command:create")), Depends(require_legacy_command_api)])
@router.post("/submit", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_permission("command:create")), Depends(require_legacy_command_api)])
async def submit_command(
    payload: SubmitCommandRequest,
    initiator_identity: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """
    Единая точка входа (Command Bus) для запуска всех инфраструктурных и сервисных операций.
    1. Проверка идемпотентности (по idempotency_key).
    2. Сохранение записи в PostgreSQL `job_log`.
    3. Кэширование состояния в Redis `execution_job:{job_id}`.
    4. Публикация в Redis Stream `stream:execution_queue` и Pub/Sub канал событий.
    """
    initiator_str = payload.initiator or initiator_identity or "unknown"
    source_str = payload.source or ("web" if "admin" in initiator_identity else "cli")

    # 1. Проверка идемпотентности
    if payload.idempotency_key:
        stmt = select(JobLog).where(JobLog.idempotency_key == payload.idempotency_key)
        res = await db.execute(stmt)
        existing_job = res.scalar_one_or_none()
        if existing_job:
            logger.info(
                "Обнаружена повторная команда с idempotency_key=%s (Job ID: %s)",
                payload.idempotency_key,
                existing_job.id,
            )
            return {
                "status": "accepted",
                "job_id": str(existing_job.id),
                "command_type": existing_job.command_type,
                "current_status": existing_job.status,
                "is_duplicate": True,
                "created_at": existing_job.created_at.isoformat() if existing_job.created_at else None,
            }

    # Извлечение task_id из target или params
    task_id = payload.target.get("task_id") or payload.params.get("task_id")
    if task_id is not None:
        try:
            task_id = int(task_id)
        except (ValueError, TypeError):
            task_id = None

    if payload.suggestion_task_id is not None:
        if task_id != payload.suggestion_task_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI-предложение относится к другой заявке.",
            )
        try:
            await require_current_suggestion(
                get_redis_client(), payload.suggestion_task_id, payload.suggestion_fingerprint
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    job_uuid = uuid.uuid4()
    job_id = f"job_{job_uuid.hex[:12]}"

    # Проверка политики исполнения и Killswitch
    policy_engine = get_policy_engine()
    effective_mode, is_allowed, reason = await policy_engine.evaluate_execution_mode(
        action_id=payload.type,
        requested_mode=payload.mode,
    )
    if not is_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=reason,
        )
    command_mode = effective_mode

    # Режим dry_run (симуляция)
    if command_mode == "dry_run" or payload.mode == "dry_run":
        return {
            "status": "dry_run_success",
            "job_id": job_id,
            "command_type": payload.type,
            "target": payload.target,
            "params": payload.params,
            "message": f"Симуляция выполнения команды '{payload.type}' прошла успешно (изменения не вносились).",
        }

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # 2. Сохранение в PostgreSQL job_log
    new_job_log = JobLog(
        id=job_uuid,
        idempotency_key=payload.idempotency_key,
        command_type=payload.type,
        target_json=payload.target,
        params_json=payload.params,
        mode=command_mode,
        initiator=initiator_str,
        source=source_str,
        status="queued",
        priority=payload.priority,
        task_id=task_id,
        created_at=now_utc,
    )
    db.add(new_job_log)
    try:
        await db.commit()
    except Exception as e:
        logger.exception("Ошибка сохранения JobLog в PostgreSQL: %s", e)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Команда не поставлена в очередь: PostgreSQL недоступен.",
        ) from e

    # 3. Сохранение состояния в Redis
    job_data = {
        "job_id": job_id,
        "uuid": str(job_uuid),
        "action": payload.type,
        "command_type": payload.type,
        "task_id": task_id or 0,
        "target": payload.target,
        "params": payload.params,
        "mode": command_mode,
        "initiator": initiator_str,
        "source": source_str,
        "priority": payload.priority,
        "auto_close_ticket": payload.auto_close_ticket,
        "status": "queued",
        "created_at": time.time(),
        "suggestion_task_id": payload.suggestion_task_id,
        "suggestion_fingerprint": payload.suggestion_fingerprint,
    }

    try:
        r = get_redis_client()
        await r.set(
            f"execution_job:{job_id}",
            json.dumps(job_data, ensure_ascii=False),
            ex=3600 * 24 * 7,  # 7 дней TTL в Redis для активных задач
        )

        # 4. Постановка в очередь Redis Stream
        await r.xadd(
            STREAM_EXECUTION_QUEUE,
            {
                "job_id": job_id,
                "uuid": str(job_uuid),
                "action": payload.type,
                "task_id": str(task_id or 0),
                "payload": json.dumps(payload.params, ensure_ascii=False),
                "target": json.dumps(payload.target, ensure_ascii=False),
                "mode": command_mode,
                "auto_close": str(payload.auto_close_ticket).lower(),
                "initiator": initiator_str,
            },
            maxlen=10000,
            approximate=True,
        )

        # 5. Оповещение в Pub/Sub канал событий
        event_payload = {
            "event": "queued",
            "job_id": job_id,
            "command_type": payload.type,
            "target": payload.target,
            "mode": command_mode,
            "initiator": initiator_str,
            "timestamp": time.time(),
        }
        event_str = json.dumps(event_payload, ensure_ascii=False)
        await r.publish(f"job:{job_id}:events", event_str)
        await r.publish("events:all", event_str)

        logger.info(
            "Команда %s [%s] успешно поставлена в Command Bus (initiator=%s, mode=%s)",
            job_id,
            payload.type,
            initiator_str,
            command_mode,
        )

        return {
            "status": "accepted",
            "job_id": job_id,
            "command_type": payload.type,
            "mode": command_mode,
            "task_id": task_id,
            "initiator": initiator_str,
            "created_at": now_utc.isoformat(),
        }

    except Exception as e:
        logger.exception("Ошибка постановки команды %s в Redis: %s", job_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка постановки команды в очередь: {e}",
        )


@router.get("/{job_id}", status_code=status.HTTP_200_OK)
async def get_command_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Возвращает текущий статус, прогресс и результат выполнения команды по job_id.
    Сначала проверяет оперативный кэш Redis, затем базу данных PostgreSQL.
    """
    r = get_redis_client()
    raw = await r.get(f"execution_job:{job_id}")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass

    # Поиск по UUID или префиксу в PostgreSQL
    try:
        clean_uuid = job_id.replace("job_", "")
        if len(clean_uuid) == 32 or len(clean_uuid) == 36:
            stmt = select(JobLog).where(JobLog.id == uuid.UUID(clean_uuid))
        else:
            stmt = (
                select(JobLog)
                .where(JobLog.id.cast(JobLog.id.type).like(f"{clean_uuid}%"))
                .limit(1)
            )
        res = await db.execute(stmt)
        record = res.scalar_one_or_none()
        if record:
            return {
                "job_id": job_id,
                "command_type": record.command_type,
                "status": record.status,
                "mode": record.mode,
                "initiator": record.initiator,
                "source": record.source,
                "task_id": record.task_id,
                "target": record.target_json,
                "params": record.params_json,
                "result": record.result_json,
                "error_message": record.error_message,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "started_at": record.started_at.isoformat() if record.started_at else None,
                "completed_at": record.completed_at.isoformat() if record.completed_at else None,
            }
    except Exception as e:
        logger.debug("Ошибка поиска JobLog в Postgres: %s", e)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Команда '{job_id}' не найдена.",
    )


@router.get("", status_code=status.HTTP_200_OK)
async def list_audit_log(
    status_filter: str | None = Query(None, alias="status", description="Фильтр по статусу"),
    command_type: str | None = Query(None, description="Фильтр по типу команды"),
    initiator: str | None = Query(None, description="Фильтр по инициатору"),
    task_id: int | None = Query(None, description="Фильтр по ID заявки"),
    limit: int = Query(50, ge=1, le=100, description="Количество записей"),
    offset: int = Query(0, ge=0, description="Смещение пагинации"),
    db: AsyncSession = Depends(get_db),
):
    """
    Возвращает историю выполнения команд (Audit Trail) из персистентной таблицы job_log.
    """
    stmt = select(JobLog)
    count_stmt = select(func.count(JobLog.id))

    if status_filter:
        stmt = stmt.where(JobLog.status == status_filter)
        count_stmt = count_stmt.where(JobLog.status == status_filter)
    if command_type:
        stmt = stmt.where(JobLog.command_type == command_type)
        count_stmt = count_stmt.where(JobLog.command_type == command_type)
    if initiator:
        stmt = stmt.where(JobLog.initiator.ilike(f"%{initiator}%"))
        count_stmt = count_stmt.where(JobLog.initiator.ilike(f"%{initiator}%"))
    if task_id:
        stmt = stmt.where(JobLog.task_id == task_id)
        count_stmt = count_stmt.where(JobLog.task_id == task_id)

    stmt = stmt.order_by(desc(JobLog.created_at)).limit(limit).offset(offset)

    total_res = await db.execute(count_stmt)
    total = total_res.scalar_one() or 0

    items_res = await db.execute(stmt)
    records = items_res.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "job_id": f"job_{r.id.hex[:12]}",
                "id": str(r.id),
                "idempotency_key": r.idempotency_key,
                "command_type": r.command_type,
                "status": r.status,
                "mode": r.mode,
                "initiator": r.initiator,
                "source": r.source,
                "task_id": r.task_id,
                "target": r.target_json,
                "params": r.params_json,
                "result": r.result_json,
                "error_message": r.error_message,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in records
        ],
    }


@router.post("/{job_id}/confirm", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("command:approve:r1")), Depends(require_legacy_command_api)])
async def confirm_command(
    job_id: str,
    payload: ConfirmDecisionRequest,
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """
    Принимает решение оператора (Human-in-the-Loop) для команды в статусе ожидания подтверждения.
    Передаёт решение исполнителю через Redis queue `job:{job_id}:confirm` и публикует событие в Pub/Sub.
    """
    decision = payload.decision.lower().strip()
    if decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Недопустимое решение. Ожидается 'approve' или 'reject'.",
        )

    r = get_redis_client()
    raw = await r.get(f"execution_job:{job_id}")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Команда '{job_id}' не найдена.",
        )

    job_data = json.loads(raw)
    if job_data.get("mode") != "confirm":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Подтверждение доступно только для команд в режиме HITL (confirm).",
        )

    # 1. Отправляем решение в блокирующую очередь воркера
    confirm_msg = {
        "decision": decision,
        "reason": payload.reason or "",
        "operator": payload.operator or operator,
        "timestamp": time.time(),
    }
    await r.lpush(f"job:{job_id}:confirm", json.dumps(confirm_msg, ensure_ascii=False))

    # 2. Обновляем статус в Redis
    if decision == "reject":
        job_data["status"] = "cancelled"
        job_data["message"] = f"Отклонено оператором {payload.operator or operator}: {payload.reason or 'без причины'}"
    else:
        job_data["status"] = "confirmed"
    await r.set(f"execution_job:{job_id}", json.dumps(job_data, ensure_ascii=False), ex=3600 * 24 * 7)

    # 3. Публикуем событие
    event_payload = {
        "event": "confirm_decision",
        "job_id": job_id,
        "decision": decision,
        "operator": payload.operator or operator,
        "reason": payload.reason,
        "timestamp": time.time(),
    }
    event_str = json.dumps(event_payload, ensure_ascii=False)
    await r.publish(f"job:{job_id}:events", event_str)
    await r.publish("events:all", event_str)

    # 4. Обновляем в PostgreSQL, если отклонено
    if decision == "reject":
        try:
            clean_uuid = job_id.replace("job_", "")
            if len(clean_uuid) == 32 or len(clean_uuid) == 36:
                stmt = select(JobLog).where(JobLog.id == uuid.UUID(clean_uuid))
                res = await db.execute(stmt)
                record = res.scalar_one_or_none()
                if record:
                    record.status = "cancelled"
                    record.error_message = f"Отклонено оператором {payload.operator or operator}"
                    record.completed_at = datetime.datetime.now(datetime.timezone.utc)
                    await db.commit()
        except Exception as e:
            logger.debug("Ошибка обновления статуса отклонения в БД: %s", e)

    return {
        "status": "decision_recorded",
        "job_id": job_id,
        "decision": decision,
        "operator": payload.operator or operator,
    }


@router.post("/{job_id}/cancel", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("command:cancel")), Depends(require_legacy_command_api)])
async def cancel_command(
    job_id: str,
    reason: str = Query("Отменено пользователем", description="Причина отмены"),
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """
    Отменяет выполнение ожидающей задачи.
    """
    r = get_redis_client()
    raw = await r.get(f"execution_job:{job_id}")
    if raw:
        job_data = json.loads(raw)
        job_data["status"] = "cancelled"
        job_data["message"] = f"Отменено: {reason}"
        await r.set(f"execution_job:{job_id}", json.dumps(job_data, ensure_ascii=False), ex=3600 * 24 * 7)

    # Отправляем reject в очередь подтверждений, если задача ждала HITL
    await r.lpush(
        f"job:{job_id}:confirm",
        json.dumps({"decision": "reject", "reason": reason, "operator": operator}, ensure_ascii=False),
    )

    # Публикация события
    event_payload = {
        "event": "cancelled",
        "job_id": job_id,
        "reason": reason,
        "operator": operator,
        "timestamp": time.time(),
    }
    event_str = json.dumps(event_payload, ensure_ascii=False)
    await r.publish(f"job:{job_id}:events", event_str)
    await r.publish("events:all", event_str)

    return {"status": "cancelled", "job_id": job_id, "reason": reason}
