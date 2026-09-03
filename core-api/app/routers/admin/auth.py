import contextlib
import json
import logging
from datetime import datetime, timedelta, UTC

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.config import settings
from app.routers.deps import verify_admin_jwt
from app.services.crypto import decrypt_token, encrypt_token
from app.services.intraservice import verify_credentials
from app.services.worker import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class DomainAuthRequest(BaseModel):
    username: str
    password: str | None = None


@router.post("/admin/api/login")
async def admin_login(payload: LoginRequest, response: Response):
    """
    Проверяет учетные данные администратора/оператора в IntraService.
    При успехе сохраняет зашифрованный токен сессии оператора в Redis
    и устанавливает подписанный JWT токен в HttpOnly Cookie.
    """
    auth_b64, user_id = await verify_credentials(payload.username, payload.password)
    if not auth_b64:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )

    # Сохраняем учетные данные оператора в изолированном ключе Redis с TTL 12 часов
    try:
        r = get_redis_client()
        encrypted_auth = encrypt_token(auth_b64)
        await r.set(f"admin_auth:{payload.username}", encrypted_auth, ex=12 * 3600)
        logger.info(
            "Учетные данные оператора '%s' (user_id: %s) сохранены в Redis (сессия 12ч)",
            payload.username,
            user_id,
        )
    except Exception as e:
        logger.error(
            "Не удалось сохранить учетные данные оператора в Redis: %s", e
        )

    expire = datetime.now(UTC) + timedelta(hours=12)
    token_data = {"sub": payload.username, "user_id": user_id, "exp": expire}
    token = jwt.encode(token_data, settings.JWT_SECRET or "", algorithm="HS256")

    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        max_age=12 * 3600,
        expires=expire,
        samesite="lax",
        secure=False,
    )
    return {"status": "success", "username": payload.username}


@router.post("/admin/api/logout")
async def admin_logout(response: Response, username: str = Depends(verify_admin_jwt)):
    """
    Удаляет куку сессии администратора и очищает токен сессии оператора из Redis.
    """
    try:
        r = get_redis_client()
        await r.delete(f"admin_auth:{username}")
    except Exception as e:
        logger.warning("Ошибка при удалении сессии оператора из Redis: %s", e)

    response.delete_cookie(key="admin_session")
    return {"status": "success"}


@router.get("/admin/api/me")
async def admin_me(username: str = Depends(verify_admin_jwt)):
    """
    Возвращает информацию о текущем авторизованном администраторе.
    """
    return {"username": username}


from app.database.db import get_db
from app.services import vault
from sqlalchemy.ext.asyncio import AsyncSession


@router.post("/admin/api/domain-auth", dependencies=[Depends(verify_admin_jwt)])
async def set_domain_auth(payload: DomainAuthRequest, db: AsyncSession = Depends(get_db)):
    """
    Сохраняет доменную учетную запись (WinRM + LDAPS) в PostgreSQL (SSOT)
    с автоматическим прогревом токена в Redis (worker:domain_auth).
    """
    try:
        await vault.save_domain_credentials(
            db,
            username=payload.username,
            password=payload.password,
        )
        return {"status": "success"}
    except Exception as e:
        logger.exception("Ошибка при сохранении доменной учетной записи: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось сохранить учетную запись: {e}",
        ) from e


@router.get("/admin/api/domain-auth", dependencies=[Depends(verify_admin_jwt)])
async def get_domain_auth_status(db: AsyncSession = Depends(get_db)):
    """
    Возвращает статус настройки доменной учетной записи из SSOT Vault.
    Пароль не возвращается в целях безопасности.
    """
    status_info = await vault.get_vault_status(db)
    dom = status_info.get("domain", {})
    return {
        "is_configured": dom.get("is_configured", False),
        "username": dom.get("username"),
    }
