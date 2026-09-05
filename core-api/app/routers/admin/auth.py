import contextlib
import json
import logging
from datetime import datetime, timedelta, UTC

import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
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

    admin_logins = [
        u.strip().lower() for u in (settings.ADMIN_LOGINS or "").split(",") if u.strip()
    ]
    is_admin = payload.username.strip().lower() in admin_logins
    role = "admin" if is_admin else "operator"

    expire = datetime.now(UTC) + timedelta(hours=12)
    token_data = {
        "sub": payload.username,
        "user_id": user_id,
        "role": role,
        "is_admin": is_admin,
        "exp": expire,
    }
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
    return {
        "status": "success",
        "username": payload.username,
        "role": role,
        "is_admin": is_admin,
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 12 * 3600,
    }


@router.post("/admin/api/logout")
async def admin_logout(response: Response, admin_session: str | None = Cookie(None)):
    """
    Удаляет куку сессии администратора и очищает токен сессии оператора из Redis.
    """
    if admin_session:
        try:
            payload = jwt.decode(
                admin_session, settings.JWT_SECRET or "", algorithms=["HS256"]
            )
            username = payload.get("sub")
            if username:
                r = get_redis_client()
                await r.delete(f"admin_auth:{username}")
        except Exception as e:
            logger.warning("Ошибка при удалении сессии оператора из Redis: %s", e)

    response.delete_cookie(key="admin_session")
    return {"status": "success"}


@router.get("/admin/api/me")
async def admin_me(
    admin_session: str | None = Cookie(None),
    authorization: str | None = Header(None),
):
    """
    Возвращает информацию о текущем авторизованном пользователе и его роли.
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif admin_session:
        token = admin_session.strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия не найдена. Требуется авторизация.",
        )

    try:
        secret = settings.JWT_SECRET
        payload = None
        for sec in [secret]:
            if not sec:
                continue
            try:
                payload = jwt.decode(token, sec, algorithms=["HS256"])
                break
            except jwt.PyJWTError:
                continue

        if not payload or not payload.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Некорректная сессия.",
            )

        username = payload.get("sub")
        admin_logins = [
            u.strip().lower() for u in (settings.ADMIN_LOGINS or "").split(",") if u.strip()
        ]
        is_admin = (payload.get("role") == "admin") or (
            username.strip().lower() in admin_logins
        )

        return {
            "username": username,
            "user_id": payload.get("user_id"),
            "role": "admin" if is_admin else "operator",
            "is_admin": is_admin,
            "access_token": token,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительная сессия.",
        )


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
