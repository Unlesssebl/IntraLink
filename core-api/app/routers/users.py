from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.database.db import get_db, User
from app.models.schemas import UserResponse, UserStateUpdate
from app.routers.deps import verify_api_key, get_user_by_tg_id

router = APIRouter(
    prefix="/users",
    tags=["Users"],
    dependencies=[Depends(verify_api_key)]
)

@router.get("", response_model=List[UserResponse], status_code=status.HTTP_200_OK)
async def get_all_users(
    db: AsyncSession = Depends(get_db)
):
    """
    Получить список всех зарегистрированных пользователей.
    Используется планировщиком для периодического опроса обновлений.
    """
    query = select(User)
    result = await db.execute(query)
    users = result.scalars().all()
    return users

@router.get("/{tg_user_id}", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def get_user(
    user: User = Depends(get_user_by_tg_id)
):
    """
    Получить информацию о пользователе по его Telegram ID.
    """
    return user

@router.patch("/{tg_user_id}/state", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def update_user_state(
    payload: UserStateUpdate,
    user: User = Depends(get_user_by_tg_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Обновить состояние периодического опроса пользователя (последняя проверенная задача, комментарий, время проверки).
    """
    if payload.last_task_id is not None:
        user.last_task_id = payload.last_task_id
    if payload.last_comment_id is not None:
        user.last_comment_id = payload.last_comment_id
    if payload.last_check_time is not None:
        user.last_check_time = payload.last_check_time
        
    await db.commit()
    await db.refresh(user)
    
    return user
