from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database.db import get_db, User
from app.models.schemas import LoginRequest, LoginResponse
from app.services.intraservice import verify_credentials
from app.routers.deps import verify_api_key
from app.services.crypto import encrypt_token

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
    dependencies=[Depends(verify_api_key)]
)

@router.post("/login", response_model=LoginResponse, status_code=status.HTTP_200_OK)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Авторизация пользователя.
    Проверяет данные в IntraService, затем сохраняет или обновляет сессию пользователя в БД.
    """
    # 1. Проверяем учетные данные в IntraService API
    auth_b64, user_id = await verify_credentials(payload.login, payload.password)
    
    if not auth_b64 or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверное имя пользователя или пароль в IntraService."
        )
        
    encrypted_auth_b64 = encrypt_token(auth_b64)
        
    # 2. Ищем пользователя в локальной БД
    query = select(User).where(User.tg_user_id == payload.tg_user_id)
    result = await db.execute(query)
    db_user = result.scalar_one_or_none()
    
    if db_user:
        # Обновляем существующего пользователя
        db_user.is_login = payload.login
        db_user.is_password_b64 = encrypted_auth_b64
        db_user.is_user_id = user_id
    else:
        # Создаем нового пользователя
        db_user = User(
            tg_user_id=payload.tg_user_id,
            is_login=payload.login,
            is_password_b64=encrypted_auth_b64,
            is_user_id=user_id
        )
        db.add(db_user)
        
    await db.commit()
    
    return LoginResponse(
        status="success",
        message="Авторизация успешно пройдена и сохранена.",
        is_user_id=user_id
    )

@router.delete("/logout", status_code=status.HTTP_200_OK)
async def logout(
    tg_user_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Выход пользователя.
    Удаляет учетные данные пользователя по его Telegram ID.
    """
    query = delete(User).where(User.tg_user_id == tg_user_id)
    result = await db.execute(query)
    await db.commit()
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Пользователь с Telegram ID {tg_user_id} не найден."
        )
        
    return {"status": "success", "message": "Сессия пользователя удалена."}
