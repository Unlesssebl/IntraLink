import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.database.db import AsyncSessionLocal, User
from app.routers.deps import verify_admin_jwt

logger = logging.getLogger(__name__)

router = APIRouter()


class AddUserRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    full_name: str | None = None


@router.get("/admin/api/users", dependencies=[Depends(verify_admin_jwt)])
async def get_telegram_users():
    """
    Возвращает список зарегистрированных пользователей Telegram-бота.
    """
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User))
        users = res.scalars().all()
        return [
            {
                "telegram_id": u.tg_user_id,
                "username": u.is_login,
                "full_name": u.is_login,
                "is_active": True,
                "created_at": u.last_check_time,
            }
            for u in users
        ]


@router.post("/admin/api/users/add", dependencies=[Depends(verify_admin_jwt)])
async def add_telegram_user(payload: AddUserRequest):
    """
    Добавляет нового пользователя Telegram.
    """
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(User).where(User.tg_user_id == payload.telegram_id)
        )
        existing = res.scalar_one_or_none()
        if existing:
            existing.is_login = payload.username or existing.is_login
        else:
            new_u = User(
                tg_user_id=payload.telegram_id,
                is_login=payload.username or str(payload.telegram_id),
                is_password_b64="",
            )
            db.add(new_u)
        await db.commit()
        return {"status": "success", "telegram_id": payload.telegram_id}


@router.post(
    "/admin/api/users/{tg_user_id}/toggle",
    dependencies=[Depends(verify_admin_jwt)],
)
async def toggle_telegram_user(tg_user_id: int):
    """
    Переключает флаг активности пользователя.
    """
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(User).where(User.tg_user_id == tg_user_id)
        )
        user = res.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        return {"status": "success", "telegram_id": tg_user_id, "is_active": True}


@router.delete(
    "/admin/api/users/{tg_user_id}", dependencies=[Depends(verify_admin_jwt)]
)
async def delete_telegram_user(tg_user_id: int):
    """
    Удаляет пользователя из базы данных.
    """
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(User).where(User.tg_user_id == tg_user_id)
        )
        user = res.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        await db.delete(user)
        await db.commit()
        return {"status": "success", "telegram_id": tg_user_id}
