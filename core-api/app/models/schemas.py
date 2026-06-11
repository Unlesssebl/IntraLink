from pydantic import BaseModel


# Схемы для авторизации
class LoginRequest(BaseModel):
    tg_user_id: int
    login: str
    password: str


class LoginResponse(BaseModel):
    status: str
    message: str
    is_user_id: int | None = None


# Схемы для пользователей
class UserResponse(BaseModel):
    tg_user_id: int
    is_login: str
    is_user_id: int | None = None
    last_task_id: int
    last_comment_id: int
    last_check_time: str | None = None

    class Config:
        from_attributes = True


class UserStateUpdate(BaseModel):
    last_task_id: int | None = None
    last_comment_id: int | None = None
    last_check_time: str | None = None
