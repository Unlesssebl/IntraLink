import secrets
import jwt

from fastapi import Depends, Header, HTTPException, Query, status, Cookie
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import User, get_db


async def verify_api_key(
    x_bot_api_key: str | None = Header(
        None, alias="X-Bot-Api-Key", description="API-ключ бота для доступа к Core API"
    ),
    api_key: str | None = Query(
        None, description="API-ключ в query-параметрах для SSE"
    ),
) -> str:
    """
    Зависимость для проверки API-ключа бота.
    Сравнивает переданный заголовок X-Bot-Api-Key или query-параметр api_key
    с настроенным в конфигурации.
    """
    key_to_check = x_bot_api_key or api_key
    if not key_to_check:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="API-ключ не предоставлен."
        )

    if not settings.BOT_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ключ авторизации BOT_API_KEY не сконфигурирован на сервере.",
        )

    # Используем secrets.compare_digest для предотвращения атак по времени
    # (Timing Attacks)
    if not secrets.compare_digest(key_to_check, settings.BOT_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий API-ключ.",
        )
    return key_to_check


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
    return user


async def verify_admin_jwt(
    authorization: str | None = Header(None, alias="Authorization"),
    admin_session: str | None = Cookie(None),
) -> str:
    """
    Зависимость для проверки сессии администратора по JWT токену из Cookie или Authorization Header.
    Проверяет принадлежность пользователя к утвержденному списку ADMIN_LOGINS или роль 'admin'.
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

    secrets_to_try = [settings.ADMIN_JWT_SECRET, settings.JWT_SECRET, "intralink-admin-secret"]
    payload = None
    last_err = None
    for sec in secrets_to_try:
        if not sec:
            continue
        try:
            payload = jwt.decode(token, sec, algorithms=["HS256"])
            break
        except jwt.ExpiredSignatureError as e:
            last_err = e
            break
        except jwt.InvalidTokenError as e:
            last_err = e
            continue

    if not payload:
        if isinstance(last_err, jwt.ExpiredSignatureError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Время действия сессии истекло.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен сессии.",
        )

    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректный токен сессии.",
        )

    admin_logins = [
        u.strip().lower() for u in (settings.ADMIN_LOGINS or "").split(",") if u.strip()
    ]
    is_admin = (payload.get("role") == "admin") or (str(username).lower() in admin_logins)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Недостаточно прав: учетная запись '{username}' не входит в список администраторов.",
        )

    return str(username)


async def verify_admin_or_api_key(
    x_bot_api_key: str | None = Header(
        None, alias="X-Bot-Api-Key", description="API-ключ бота для доступа к Core API"
    ),
    api_key: str | None = Query(
        None, description="API-ключ в query-параметрах для SSE"
    ),
    authorization: str | None = Header(None, alias="Authorization"),
    admin_session: str | None = Cookie(None),
    token_query: str | None = Query(None, alias="token"),
) -> str:
    """
    Универсальная зависимость: принимает либо сессию администратора (JWT Header/Cookie/Query token),
    либо API-ключ (X-Bot-Api-Key или query api_key).
    """
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_val = authorization[7:].strip()
        if bearer_val and bearer_val != "sso_session":
            token = bearer_val
    elif admin_session:
        token = admin_session.strip()
    elif token_query:
        token = token_query.strip()

    if token:
        for sec in [settings.ADMIN_JWT_SECRET, settings.JWT_SECRET, "intralink-admin-secret"]:
            if not sec:
                continue
            try:
                payload = jwt.decode(token, sec, algorithms=["HS256"])
                username = payload.get("sub")
                if username:
                    return str(username)
            except Exception:
                pass

    key_to_check = x_bot_api_key or api_key
    if key_to_check and settings.BOT_API_KEY and secrets.compare_digest(key_to_check, settings.BOT_API_KEY):
        return "bot_or_cli"

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Требуется авторизация (сессия администратора или API-ключ).",
    )


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
        for sec in [settings.ADMIN_JWT_SECRET, settings.JWT_SECRET, "intralink-admin-secret"]:
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
        for sec in [settings.ADMIN_JWT_SECRET, settings.JWT_SECRET, "intralink-admin-secret"]:
            if not sec:
                continue
            try:
                payload = jwt.decode(token, sec, algorithms=["HS256"])
                username = payload.get("sub")
                user_id = payload.get("user_id")
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
    username: str = Depends(verify_admin_jwt),
) -> str:
    """
    Получает расшифрованный Basic Auth токен авторизованного оператора из Redis.
    Сессия оператора изолирована и сохраняется под ключом admin_auth:{username}.
    """
    from app.services.crypto import decrypt_token
    from app.services.worker import get_redis_client

    r = get_redis_client()
    encrypted_auth = await r.get(f"admin_auth:{username}")
    if not encrypted_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия оператора истекла или не найдена. Пожалуйста, войдите заново.",
        )
    return decrypt_token(encrypted_auth)

