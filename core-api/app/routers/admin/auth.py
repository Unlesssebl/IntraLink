import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import get_db, init_db
from app.routers.deps import authenticate_request, require_permission
from app.services.crypto import encrypt_token
from app.services.identity import (
    PrincipalContext,
    ensure_human_principal,
    get_roles,
    issue_session,
    revoke_refresh_token,
    rotate_refresh_session,
)
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


def _set_session_cookies(
    response: Response, access_token: str, refresh_token: str, expires_in: int
) -> None:
    secure = settings.APP_ENV == "production"
    response.set_cookie(
        "admin_session",
        access_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=expires_in,
    )
    response.set_cookie(
        "refresh_session",
        refresh_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=settings.REFRESH_SESSION_TTL_HOURS * 3600,
        path="/admin/api",
    )


@router.post("/admin/api/login")
async def admin_login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Проверяет учетные данные администратора/оператора в IntraService.
    При успехе сохраняет зашифрованный токен сессии оператора в Redis
    и устанавливает подписанный JWT токен в HttpOnly Cookie.
    """
    if settings.DATABASE_URL.startswith("sqlite"):
        await init_db()
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
        await r.set(
            f"admin_auth:{payload.username.strip().lower()}",
            encrypted_auth,
            ex=settings.REFRESH_SESSION_TTL_HOURS * 3600,
        )
        logger.info(
            "Учетные данные оператора '%s' (user_id: %s) сохранены в Redis (сессия 12ч)",
            payload.username,
            user_id,
        )
    except Exception as e:
        logger.error(
            "Не удалось сохранить учетные данные оператора в Redis: %s", e
        )

    principal = await ensure_human_principal(
        db,
        username=payload.username,
        display_name=payload.username,
        external_user_id=user_id,
    )
    access_token, refresh_token, expires_in = await issue_session(
        db,
        principal=principal,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    roles = await get_roles(db, principal.id)
    is_admin = "system_admin" in roles
    role = "admin" if is_admin else "operator"
    _set_session_cookies(response, access_token, refresh_token, expires_in)
    return {
        "status": "success",
        "username": payload.username,
        "role": role,
        "is_admin": is_admin,
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


@router.post("/admin/api/refresh")
async def admin_refresh(
    response: Response,
    request: Request,
    refresh_session: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    if not refresh_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh session is missing")
    access_token, new_refresh, expires_in = await rotate_refresh_session(
        db,
        refresh_token=refresh_session,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookies(response, access_token, new_refresh, expires_in)
    return {"access_token": access_token, "token_type": "bearer", "expires_in": expires_in}


@router.post("/admin/api/logout")
async def admin_logout(
    response: Response,
    refresh_session: str | None = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Удаляет куку сессии администратора и очищает токен сессии оператора из Redis.
    """
    if refresh_session:
        await revoke_refresh_token(db, refresh_session)
    response.delete_cookie("admin_session")
    response.delete_cookie("refresh_session", path="/admin/api")
    return {"status": "success"}


@router.get("/admin/api/me")
async def admin_me(
    context: PrincipalContext = Depends(authenticate_request),
):
    """
    Возвращает информацию о текущем авторизованном пользователе и его роли.
    """
    if context.principal_type != "human":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Human session required")
    is_admin = "system_admin" in context.roles
    return {
        "username": context.subject,
        "role": "admin" if is_admin else "operator",
        "roles": sorted(context.roles),
        "permissions": sorted(context.permissions),
        "is_admin": is_admin,
    }


from app.services import vault


@router.post("/admin/api/domain-auth", dependencies=[Depends(require_permission("credentials:manage"))])
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


@router.get("/admin/api/domain-auth", dependencies=[Depends(require_permission("credentials:manage"))])
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
