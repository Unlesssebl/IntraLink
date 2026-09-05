from fastapi import APIRouter, Depends, status

from app.database.db import User
from app.models.schemas import UserResponse
from app.routers.deps import get_user_by_tg_id, require_service_scope

router = APIRouter(
    prefix="/users", tags=["Users"], dependencies=[Depends(require_service_scope("task:read"))]
)


@router.get(
    "/{tg_user_id}", response_model=UserResponse, status_code=status.HTTP_200_OK
)
async def get_user(user: User = Depends(get_user_by_tg_id)):
    """
    Получить информацию о пользователе по его Telegram ID.
    """
    return user
