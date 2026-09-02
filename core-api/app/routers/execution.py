"""
Роутер асинхронного Execution Broker для удаленного запуска задач в Windows-домене.
"""

import json
import logging
import time
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

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
async def enqueue_execution_job(payload: EnqueueExecutionRequest):
    """
    Ставит задачу исполнения в Redis Stream для фонового Windows Execution Worker.
    """
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    job_data = {
        "job_id": job_id,
        "action": payload.action,
        "task_id": payload.task_id or 0,
        "params": payload.params,
        "auto_close_ticket": payload.auto_close_ticket,
        "status": "queued",
        "created_at": time.time(),
    }

    try:
        r = get_redis_client()
        # Сохраняем состояние задачи в ключ с TTL
        await r.set(
            f"execution_job:{job_id}",
            json.dumps(job_data, ensure_ascii=False),
            ex=3600 * 24,
        )

        # Публикуем в очередь Stream
        await r.xadd(
            STREAM_EXECUTION_QUEUE,
            {
                "job_id": job_id,
                "action": payload.action,
                "task_id": str(payload.task_id or 0),
                "payload": json.dumps(payload.params, ensure_ascii=False),
                "auto_close": str(payload.auto_close_ticket).lower(),
            },
        )

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
