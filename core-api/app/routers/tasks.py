from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from app.database.db import User, get_db
from app.routers.deps import verify_api_key, get_user_by_tg_id
from app.services import intraservice

router = APIRouter(
    tags=["Tasks"],
    dependencies=[Depends(verify_api_key)]
)

class TaskCommentRequest(BaseModel):
    tg_user_id: int
    comment: str

class TaskStatusRequest(BaseModel):
    tg_user_id: int
    status_id: int

@router.get("/tasks", response_model=Any, status_code=status.HTTP_200_OK)
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

@router.get("/tasks/{task_id}", response_model=Any, status_code=status.HTTP_200_OK)
async def get_task_by_id(
    task_id: int,
    user: User = Depends(get_user_by_tg_id)
):
    """
    Получить детальную информацию по конкретной задаче, включая кастомные поля.
    Используется printer-worker для Fast-Track маршрутизации.
    """
    task = await intraservice.get_single_task(user.is_password_b64, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось получить задачу {task_id} от IntraService."
        )
    return task

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

@router.post("/tasks/{task_id}/comment", status_code=status.HTTP_200_OK)
async def add_task_comment(
    task_id: int,
    payload: TaskCommentRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Добавить комментарий к задаче.
    """
    from sqlalchemy import select
    query = select(User).where(User.tg_user_id == payload.tg_user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь с Telegram ID {payload.tg_user_id} не найден."
        )
    
    success = await intraservice.add_task_comment(user.is_password_b64, task_id, payload.comment)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось добавить комментарий к задаче {task_id}."
        )
    return {"status": "success"}

@router.post("/tasks/{task_id}/status", status_code=status.HTTP_200_OK)
async def update_task_status(
    task_id: int,
    payload: TaskStatusRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Обновить статус задачи.
    """
    from sqlalchemy import select
    query = select(User).where(User.tg_user_id == payload.tg_user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь с Telegram ID {payload.tg_user_id} не найден."
        )
    
    success = await intraservice.update_task_status(user.is_password_b64, task_id, payload.status_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось обновить статус задачи {task_id}."
        )
    return {"status": "success"}

