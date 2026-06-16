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
    admin_session: str | None = Cookie(None),
) -> str:
    """
    Зависимость для проверки сессии администратора по JWT токену из Cookie.
    """
    if not admin_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия не найдена. Требуется авторизация.",
        )
    try:
        payload = jwt.decode(admin_session, settings.JWT_SECRET or "", algorithms=["HS256"])
        username = payload.get("sub")
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Некорректный токен сессии.",
            )
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Время действия сессии истекло.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен сессии.",
        )

