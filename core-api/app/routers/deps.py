import secrets
import jwt

from fastapi import Cookie, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import Principal, TelegramLink, User, get_db
from app.services.identity import (
    PrincipalContext,
    ROLE_PERMISSIONS,
    authenticate_human_token,
    authenticate_service,
    record_security_event,
    require_context_permission,
)


def _decode_session_claims(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET or "",
            algorithms=["HS256"],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
        )
    except jwt.PyJWTError:
        if not settings.ALLOW_LEGACY_SHARED_KEYS:
            raise
        return jwt.decode(
            token,
            settings.JWT_SECRET or "",
            algorithms=["HS256"],
            options={"verify_aud": False},
        )


async def get_user_by_tg_id(
    tg_user_id: int, db: AsyncSession = Depends(get_db)
) -> User:
    """
    Вспомогательная зависимость для получения пользователя по его Telegram ID.
    Если пользователь не найден, бросает исключение 404.
    """
    query = select(User).where(User.tg_user_id == tg_user_id)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь с Telegram ID {tg_user_id} не найден.",
        )
    if not settings.ALLOW_LEGACY_SHARED_KEYS:
        link = await db.get(TelegramLink, tg_user_id)
        principal = await db.get(Principal, link.principal_id) if link else None
        if (
            link is None
            or link.status != "verified"
            or link.revoked_at is not None
            or principal is None
            or principal.status != "active"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Telegram identity must be linked to an active corporate principal",
            )
    return user


async def verify_trusted_origin(
    origin: str | None = Header(None, alias="Origin"),
) -> None:
    """Reject cross-site browser mutations while allowing non-browser service calls."""
    if not origin:
        return
    allowed = {
        value.strip().rstrip("/")
        for value in settings.CORS_ORIGINS.split(",")
        if value.strip()
    }
    if origin.rstrip("/") not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недоверенный Origin для изменяющего запроса.",
        )


async def authenticate_request(
    request: Request,
    authorization: str | None = Header(None, alias="Authorization"),
    access_session: str | None = Cookie(None),
    admin_session: str | None = Cookie(None),
    x_service_key_id: str | None = Header(None, alias="X-Service-Key-Id"),
    x_service_secret: str | None = Header(None, alias="X-Service-Secret"),
    x_bot_api_key: str | None = Header(None, alias="X-Bot-Api-Key"),
    x_worker_api_key: str | None = Header(None, alias="X-Worker-Api-Key"),
    api_key: str | None = Query(None),
    token_query: str | None = Query(None, alias="token"),
    db: AsyncSession = Depends(get_db),
) -> PrincipalContext:
    """Authenticate a human session or a scoped service principal."""
    if bool(x_service_key_id) != bool(x_service_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incomplete service credential")
    if x_service_key_id and x_service_secret:
        return await authenticate_service(db, key_id=x_service_key_id, secret=x_service_secret)

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()
        if bearer_token and bearer_token != "sso_session":
            token = bearer_token
    if token is None and access_session:
        token = access_session.strip()
    elif token is None and admin_session:
        token = admin_session.strip()
    elif token is None and settings.ALLOW_LEGACY_SHARED_KEYS and token_query:
        token = token_query.strip()

    if token:
        try:
            return await authenticate_human_token(db, token)
        except HTTPException:
            if not settings.ALLOW_LEGACY_SHARED_KEYS:
                raise
            try:
                payload = jwt.decode(
                    token,
                    settings.JWT_SECRET or "",
                    algorithms=["HS256"],
                    options={"verify_aud": False},
                )
            except jwt.PyJWTError as exc:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token") from exc
            subject = str(payload.get("sub") or "").strip()
            if not subject:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token")
            role = "system_admin" if payload.get("role") == "admin" else "helpdesk_operator"
            return PrincipalContext(
                principal_id=None,
                principal_type="human",
                subject=subject,
                display_name=subject,
                roles=frozenset({role}),
                grants=ROLE_PERMISSIONS[role],
                auth_method="legacy_jwt",
            )

    if settings.ALLOW_LEGACY_SHARED_KEYS:
        legacy_bot = x_bot_api_key or api_key
        if legacy_bot and settings.BOT_API_KEY and secrets.compare_digest(legacy_bot, settings.BOT_API_KEY):
            return PrincipalContext(
                principal_id=None,
                principal_type="service",
                subject="legacy-bot-client",
                display_name="Legacy bot/CLI client",
                scopes=frozenset({
                    "task:read", "task:mutate", "triage:read", "triage:mutate", "ai:use",
                    "command:read", "command:create", "command:approve:r1", "command:cancel",
                    "diagnostic:run", "events:read", "rules:manage",
                    "telegram:challenge:issue", "telegram:challenge:consume", "telegram:link",
                    "policy:manage", "command:review",
                }),
                auth_method="legacy_shared_key",
            )
        if (
            x_worker_api_key
            and settings.WORKER_API_KEY
            and secrets.compare_digest(x_worker_api_key, settings.WORKER_API_KEY)
        ):
            return PrincipalContext(
                principal_id=None,
                principal_type="service",
                subject="legacy-worker",
                display_name="Legacy worker",
                scopes=frozenset({
                    "command:claim:windows", "command:finish:windows",
                    "command:claim:backend", "command:finish:backend",
                }),
                auth_method="legacy_shared_key",
            )

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")


def require_permission(permission: str):
    async def dependency(
        request: Request,
        context: PrincipalContext = Depends(authenticate_request),
        db: AsyncSession = Depends(get_db),
    ) -> PrincipalContext:
        if not context.has(permission):
            await record_security_event(
                db,
                event_type="authorization.denied",
                outcome="denied",
                context=context,
                resource_type="http_route",
                resource_id=request.url.path,
                ip_address=request.client.host if request.client else None,
                details={"permission": permission, "method": request.method},
                commit=True,
            )
            require_context_permission(context, permission)
        return context

    dependency.__name__ = f"require_{permission.replace(':', '_')}"
    dependency.required_permission = permission
    return dependency


def require_service_scope(scope: str):
    async def dependency(
        request: Request,
        context: PrincipalContext = Depends(authenticate_request),
        db: AsyncSession = Depends(get_db),
    ) -> PrincipalContext:
        if context.principal_type != "service":
            await record_security_event(
                db,
                event_type="authorization.denied",
                outcome="denied",
                context=context,
                resource_type="http_route",
                resource_id=request.url.path,
                details={"scope": scope, "reason": "service_identity_required"},
                commit=True,
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Service identity required")
        if not context.has(scope):
            await record_security_event(
                db,
                event_type="authorization.denied",
                outcome="denied",
                context=context,
                resource_type="http_route",
                resource_id=request.url.path,
                details={"scope": scope},
                commit=True,
            )
            require_context_permission(context, scope)
        return context

    dependency.__name__ = f"require_service_{scope.replace(':', '_')}"
    dependency.required_permission = scope
    return dependency


async def principal_subject(
    context: PrincipalContext = Depends(authenticate_request),
) -> str:
    return context.subject


def require_object_permission(marker: str):
    """Marks routes whose exact permission depends on the loaded resource."""
    async def dependency(
        context: PrincipalContext = Depends(authenticate_request),
    ) -> PrincipalContext:
        return context

    dependency.__name__ = f"require_object_{marker.replace(':', '_')}"
    dependency.required_permission = marker
    return dependency


from pydantic import BaseModel


class OperatorContext(BaseModel):
    username: str
    user_id: int | None = None
    auth_b64: str
    is_service_account: bool = False


async def get_service_auth_b64(
    authorization: str | None = Header(None, alias="Authorization"),
    admin_session: str | None = Cookie(None),
    token_query: str | None = Query(None, alias="token"),
) -> str:
    """
    Получает зашифрованный токен авторизации:
    1. Если запрос от авторизованного оператора (Authorization Header, Cookie admin_session или Query ?token=...) — берем его актуальный зашифрованный токен из Redis.
    2. Иначе используем глобальный сервисный аккаунт (из ENV или Redis).
    """
    import base64
    from app.services.crypto import encrypt_token
    from app.services.worker import get_redis_client

    redis = get_redis_client()

    token = None
    if isinstance(authorization, str) and authorization.lower().startswith("bearer "):
        bearer_val = authorization[7:].strip()
        if bearer_val and bearer_val != "sso_session":
            token = bearer_val
    if not token and isinstance(admin_session, str):
        token = admin_session.strip()
    if not token and isinstance(token_query, str):
        token = token_query.strip()

    # Проверяем, есть ли активная сессия оператора
    if token:
        for sec in [settings.JWT_SECRET]:
            if not sec:
                continue
            try:
                payload = jwt.decode(token, sec, algorithms=["HS256"])
                username = payload.get("sub")
                if username:
                    try:
                        op_auth = await redis.get(f"admin_auth:{username}")
                        if op_auth and not op_auth.startswith("mock_"):
                            return op_auth
                    except Exception:
                        pass
            except Exception:
                pass

    if settings.INTRASERVICE_SERVICE_LOGIN and settings.INTRASERVICE_SERVICE_PASSWORD:
        auth_str = f"{settings.INTRASERVICE_SERVICE_LOGIN}:{settings.INTRASERVICE_SERVICE_PASSWORD}"
        plain_b64 = base64.b64encode(auth_str.encode()).decode()
        return encrypt_token(plain_b64)

    try:
        service_auth_b64 = await redis.get("worker:service_auth_b64")
        if service_auth_b64:
            return service_auth_b64
    except Exception:
        pass

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Сервисный аккаунт IntraService не настроен.",
    )


async def get_operator_context(
    authorization: str | None = Header(None, alias="Authorization"),
    admin_session: str | None = Cookie(None),
) -> OperatorContext:
    """
    Извлекает полный контекст авторизованного оператора (токен, username, user_id):
    - Если передан валидный JWT токен оператора, извлекает его реальный user_id и токен IntraService из Redis.
    - Иначе возвращает контекст сервисного аккаунта с первичным исполнителем по умолчанию.
    """
    import base64
    from app.services.crypto import encrypt_token
    from app.services.worker import get_redis_client

    redis = get_redis_client()

    token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_val = authorization[7:].strip()
        if bearer_val and bearer_val != "sso_session":
            token = bearer_val
    if not token and admin_session:
        token = admin_session.strip()

    if token:
        for sec in [settings.JWT_SECRET]:
            if not sec:
                continue
            try:
                payload = _decode_session_claims(token)
                username = payload.get("sub")
                user_id = payload.get("external_id") or payload.get("user_id")
                if username:
                    op_auth = await redis.get(f"admin_auth:{username}")
                    if op_auth:
                        return OperatorContext(
                            username=str(username),
                            user_id=int(user_id) if user_id else None,
                            auth_b64=op_auth,
                            is_service_account=False,
                        )
            except Exception:
                pass

    # Fallback на системный аккаунт
    service_auth = None
    if settings.INTRASERVICE_SERVICE_LOGIN and settings.INTRASERVICE_SERVICE_PASSWORD:
        auth_str = f"{settings.INTRASERVICE_SERVICE_LOGIN}:{settings.INTRASERVICE_SERVICE_PASSWORD}"
        plain_b64 = base64.b64encode(auth_str.encode()).decode()
        service_auth = encrypt_token(plain_b64)
    else:
        service_auth = await redis.get("worker:service_auth_b64")

    if not service_auth:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Сервисный аккаунт IntraService не настроен.",
        )

    return OperatorContext(
        username="system_service",
        user_id=settings.PRIMARY_EXECUTOR_ID,
        auth_b64=service_auth,
        is_service_account=True,
    )


async def get_operator_auth_b64(
    context: PrincipalContext = Depends(require_permission("identity:manage")),
) -> str:
    """
    Получает расшифрованный Basic Auth токен авторизованного оператора из Redis.
    Сессия оператора изолирована и сохраняется под ключом admin_auth:{username}.
    """
    from app.services.crypto import decrypt_token
    from app.services.worker import get_redis_client

    username = context.subject
    r = get_redis_client()
    encrypted_auth = await r.get(f"admin_auth:{username}")
    if not encrypted_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия оператора истекла или не найдена. Пожалуйста, войдите заново.",
        )
    return decrypt_token(encrypted_auth)

