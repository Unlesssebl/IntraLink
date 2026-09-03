from fastapi import APIRouter, Depends, status

from app.database.db import User
from app.models.schemas import UserResponse
from app.routers.deps import get_user_by_tg_id, verify_api_key

router = APIRouter(
    prefix="/users", tags=["Users"], dependencies=[Depends(verify_api_key)]
)


@router.get(
    "/{tg_user_id}", response_model=UserResponse, status_code=status.HTTP_200_OK
)
async def get_user(user: User = Depends(get_user_by_tg_id)):
    """
    Получить информацию о пользователе по его Telegram ID.
    """
    return user
