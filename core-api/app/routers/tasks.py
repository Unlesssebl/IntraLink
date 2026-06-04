from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Dict, List, Optional

from app.database.db import User
from app.routers.deps import verify_api_key, get_user_by_tg_id
from app.services import intraservice

router = APIRouter(
    tags=["Tasks"],
    dependencies=[Depends(verify_api_key)]
)

@router.get("/tasks", response_model=List[Dict[str, Any]], status_code=status.HTTP_200_OK)
async def get_tasks(
    request: Request,
    user: User = Depends(get_user_by_tg_id)
):
    """
    Получить список задач для конкретного пользователя.
    Принимает любые query-параметры фильтрации IntraService (например, statusId, page, pageSize, executorId и т.д.).
    """
    # Собираем все query-параметры, кроме tg_user_id
    filters = {}
    for key, value in request.query_params.items():
        if key != "tg_user_id":
            filters[key] = value
            
    tasks = await intraservice.get_tasks(user.is_password_b64, filters)
    if tasks is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось получить задачи от внешнего сервиса IntraService."
        )
    return tasks

@router.get("/tasks/{task_id}/lifetime", response_model=List[Dict[str, Any]], status_code=status.HTTP_200_OK)
async def get_task_lifetime(
    task_id: int,
    user: User = Depends(get_user_by_tg_id)
):
    """
    Получить историю изменений задачи.
    """
    lifetime = await intraservice.get_task_lifetime(user.is_password_b64, task_id)
    if lifetime is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось получить историю для задачи {task_id}."
        )
    return lifetime

@router.get("/statuses", response_model=List[Dict[str, Any]], status_code=status.HTTP_200_OK)
async def get_statuses(
    user: User = Depends(get_user_by_tg_id)
):
    """
    Получить справочник статусов.
    """
    statuses = await intraservice.get_statuses(user.is_password_b64)
    if statuses is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Не удалось получить справочник статусов."
        )
    return statuses
