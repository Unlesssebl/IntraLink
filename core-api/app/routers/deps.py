import secrets
from fastapi import Header, HTTPException, status, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database.db import get_db, User

async def verify_api_key(
    x_bot_api_key: str = Header(..., alias="X-Bot-Api-Key", description="API-ключ бота для доступа к Core API")
) -> str:
    """
    Зависимость для проверки API-ключа бота.
    Сравнивает переданный заголовок X-Bot-Api-Key с настроенным в конфигурации.
    """
    if not settings.BOT_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ключ авторизации BOT_API_KEY не сконфигурирован на сервере."
        )
    
    # Используем secrets.compare_digest для предотвращения атак по времени (Timing Attacks)
    if not secrets.compare_digest(x_bot_api_key, settings.BOT_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или отсутствующий API-ключ."
        )
    return x_bot_api_key

async def get_user_by_tg_id(
    tg_user_id: int,
    db: AsyncSession = Depends(get_db)
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
            detail=f"Пользователь с Telegram ID {tg_user_id} не найден."
        )
    return user
