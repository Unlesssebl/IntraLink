"""
Роутер мониторинга и управления массовыми авариями (Outage Hub).
Позволяет просматривать активные аварии, рассылать групповые оповещения заявителям
и снимать инциденты после устранения сбоев.
"""

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.routers.deps import get_service_auth_b64, verify_admin_or_api_key
from app.services import intraservice
from app.services.outage_detector import OutageDetector
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.routers.outages")

router = APIRouter(
    prefix="/api/v1/outages",
    tags=["Outage Management (Real-time AIOps)"],
    dependencies=[Depends(verify_admin_or_api_key)],
)


class ResolveOutageRequest(BaseModel):
    comment: str | None = Field(
        None, description="Опциональный комментарий при снятии инцидента"
    )


class BroadcastOutageCommentRequest(BaseModel):
    comment: str = Field(
        ..., min_length=3, description="Текст массового оповещения для всех заявок инцидента"
    )
    status_id: int | None = Field(
        None, description="Опциональный новый статус для заявок (например, 35 - На согласовании)"
    )


@router.get("/active", status_code=status.HTTP_200_OK)
async def get_active_outages():
    """
    Возвращает список всех текущих активных аварий и массовых инцидентов.
    """
    outages = await OutageDetector.get_active_outages()
    return {"total": len(outages), "outages": outages}


@router.get("/{outage_id}", status_code=status.HTTP_200_OK)
async def get_outage_details(outage_id: str):
    """
    Возвращает детальную карточку аварии по ее ID.
    """
    try:
        r = get_redis_client()
        raw = await r.get(f"outage:active:{outage_id}")
        if not raw:
            # Проверяем архив
            raw = await r.get(f"outage:archive:{outage_id}")

        if not raw:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Авария {outage_id} не найдена.",
            )

        import json
        data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
        return data
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка получения деталей аварии %s: %s", outage_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка чтения данных аварии: {e}",
        )


@router.post("/{outage_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_outage_endpoint(
    outage_id: str,
    payload: ResolveOutageRequest = ResolveOutageRequest(),
    operator: str = Depends(verify_admin_or_api_key),
):
    """
    Снимает инцидент из статуса активной аварии.
    """
    success = await OutageDetector.resolve_outage(outage_id, operator=operator)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось снять инцидент {outage_id}.",
        )
    return {"status": "success", "message": f"Инцидент {outage_id} успешно закрыт"}


@router.post("/{outage_id}/broadcast", status_code=status.HTTP_200_OK)
async def broadcast_outage_comment(
    outage_id: str,
    payload: BroadcastOutageCommentRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
    operator: str = Depends(verify_admin_or_api_key),
):
    """
    Массово добавляет комментарий инженера во все заявки, входящие в инцидент.
    """
    try:
        r = get_redis_client()
        raw = await r.get(f"outage:active:{outage_id}")
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Активная авария {outage_id} не найдена.",
            )

        import json
        outage_data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
        ticket_ids = outage_data.get("ticket_ids", [])
        if not ticket_ids:
            return {"status": "success", "affected_count": 0, "results": []}

        results = []
        for tid in ticket_ids:
            ok = await intraservice.add_task_comment(
                auth_b64=service_auth_b64,
                task_id=tid,
                comment=payload.comment,
            )
            results.append({"task_id": tid, "success": ok})

        logger.info(
            "📢 Массовое оповещение по аварии %s разослано в %s заявок оператором %s",
            outage_id,
            len(ticket_ids),
            operator,
        )
        return {
            "status": "success",
            "affected_count": len(results),
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ошибка массовой рассылки комментария по аварии %s: %s", outage_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка рассылки: {e}",
        )
