from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.routers.deps import get_service_auth_b64, verify_api_key
from app.services import intraservice

router = APIRouter(
    prefix="/service",
    tags=["Service Tasks"],
    dependencies=[Depends(verify_api_key)],
)


class ServiceTaskCommentRequest(BaseModel):
    comment: str


class ServiceTaskStatusRequest(BaseModel):
    status_id: int


class ServiceTaskExpensesRequest(BaseModel):
    minutes: int


@router.get("/tasks/{task_id}", response_model=Any, status_code=status.HTTP_200_OK)
async def get_task_by_id(task_id: int, service_auth_b64: str = Depends(get_service_auth_b64)):
    """
    Получить детальную информацию по конкретной задаче с кастомными полями
    от имени сервисного аккаунта.
    """
    task = await intraservice.get_single_task(service_auth_b64, task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось получить задачу {task_id} от IntraService.",
        )
    return task


@router.post("/tasks/{task_id}/comment", status_code=status.HTTP_200_OK)
async def add_task_comment(
    task_id: int,
    payload: ServiceTaskCommentRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
):
    """
    Добавить комментарий к задаче от имени сервисного аккаунта.
    """
    success = await intraservice.add_task_comment(
        service_auth_b64, task_id, payload.comment
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось добавить комментарий к задаче {task_id}.",
        )
    return {"status": "success"}


@router.post("/tasks/{task_id}/status", status_code=status.HTTP_200_OK)
async def update_task_status(
    task_id: int,
    payload: ServiceTaskStatusRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
):
    """
    Обновить статус задачи от имени сервисного аккаунта.
    """
    success = await intraservice.update_task_status(
        service_auth_b64, task_id, payload.status_id
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось обновить статус задачи {task_id}.",
        )
    return {"status": "success"}


@router.post("/tasks/{task_id}/expenses", status_code=status.HTTP_200_OK)
async def add_task_expenses(
    task_id: int,
    payload: ServiceTaskExpensesRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
):
    """
    Добавить трудозатраты к задаче от имени сервисного аккаунта.
    """
    success = await intraservice.add_task_expenses(
        service_auth_b64, task_id, payload.minutes
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось добавить трудозатраты к задаче {task_id}.",
        )
    return {"status": "success"}
