"""
Роутер асинхронного Execution Broker для удаленного запуска задач в Windows-домене.
Обратно-совместимый интерфейс поверх Command Bus.
"""

import datetime
import json
import logging
import time
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import JobLog, get_db
from app.routers.deps import verify_admin_or_api_key
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.routers.execution")

router = APIRouter(
    prefix="/api/v1/execution",
    tags=["Execution Broker (Windows Domain RPC)"],
    dependencies=[Depends(verify_admin_or_api_key)],
)

STREAM_EXECUTION_QUEUE = "stream:execution_queue"
STREAM_EXECUTION_RESULTS = "stream:execution_results"


class EnqueueExecutionRequest(BaseModel):
    action: str = Field(
        ...,
        description="Тип действия: grant_wlan, create_user, diagnose_host, install_printer",
    )
    task_id: int | None = Field(None, description="ID заявки в IntraService")
    params: dict[str, Any] = Field(
        default_factory=dict, description="Параметры действия"
    )
    auto_close_ticket: bool = Field(
        True,
        description="Автоматически финализировать заявку в IntraService при успехе",
    )


@router.post("/enqueue", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_execution_job(
    payload: EnqueueExecutionRequest,
    initiator_identity: str = Depends(verify_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Ставит задачу исполнения в Redis Stream и персистентную таблицу job_log для Execution Worker.
    """
    job_uuid = uuid.uuid4()
    job_id = f"job_{job_uuid.hex[:12]}"
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    # Запись в PostgreSQL job_log
    new_job_log = JobLog(
        id=job_uuid,
        command_type=payload.action,
        target_json={"task_id": payload.task_id},
        params_json=payload.params,
        mode="auto",
        initiator=initiator_identity or "unknown",
        source="execution_legacy",
        status="queued",
        task_id=payload.task_id,
        created_at=now_utc,
    )
    db.add(new_job_log)
    try:
        await db.commit()
    except Exception as e:
        logger.exception("Ошибка сохранения legacy job_log в Postgres: %s", e)
        await db.rollback()

    job_data = {
        "job_id": job_id,
        "uuid": str(job_uuid),
        "action": payload.action,
        "command_type": payload.action,
        "task_id": payload.task_id or 0,
        "params": payload.params,
        "auto_close_ticket": payload.auto_close_ticket,
        "status": "queued",
        "initiator": initiator_identity or "unknown",
        "created_at": time.time(),
    }

    try:
        r = get_redis_client()
        # Сохраняем состояние задачи в ключ с TTL 7 дней
        await r.set(
            f"execution_job:{job_id}",
            json.dumps(job_data, ensure_ascii=False),
            ex=3600 * 24 * 7,
        )

        # Публикуем в очередь Stream
        await r.xadd(
            STREAM_EXECUTION_QUEUE,
            {
                "job_id": job_id,
                "uuid": str(job_uuid),
                "action": payload.action,
                "task_id": str(payload.task_id or 0),
                "payload": json.dumps(payload.params, ensure_ascii=False),
                "auto_close": str(payload.auto_close_ticket).lower(),
                "initiator": initiator_identity or "unknown",
            },
        )

        # Оповещение в Pub/Sub
        event_payload = {
            "event": "queued",
            "job_id": job_id,
            "command_type": payload.action,
            "task_id": payload.task_id,
            "timestamp": time.time(),
        }
        event_str = json.dumps(event_payload, ensure_ascii=False)
        await r.publish(f"job:{job_id}:events", event_str)
        await r.publish("events:all", event_str)

        logger.info(
            "Задача %s [%s] успешно поставлена в очередь исполнения",
            job_id,
            payload.action,
        )
        return {
            "status": "accepted",
            "job_id": job_id,
            "action": payload.action,
            "task_id": payload.task_id,
        }
    except Exception as e:
        logger.exception("Ошибка постановки задачи %s в очередь: %s", job_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка Redis при постановке задачи: {e}",
        )


@router.get("/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def get_execution_job_status(job_id: str):
    """
    Возвращает текущий статус и результат выполнения задачи в Windows-домене.
    """
    try:
        r = get_redis_client()
        raw = await r.get(f"execution_job:{job_id}")
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Задача '{job_id}' не найдена.",
            )
        data = json.loads(raw)
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка получения статуса задачи %s: %s", job_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка Redis: {e}",
        )

