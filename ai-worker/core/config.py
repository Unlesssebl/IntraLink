import logging
import os
import secrets

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def read_secret_file(file_path_env: str) -> str | None:
    path = os.getenv(file_path_env)
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error("Ошибка чтения файла секрета %s (%s): %s", file_path_env, path, e)
    return None


class Settings(BaseSettings):
    CORE_API_URL: str = Field("http://localhost:8000/api/v1", description="URL-адрес API Core Gateway")
    BOT_API_KEY: str | None = Field(None, description="Предоставленный API-ключ для авторизации бота")
    INTRASERVICE_URL: str = Field("http://localhost:8000/api/", description="URL-адрес API IntraService")
    DATABASE_URL: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/intraservice",
        description="Строка подключения к базе данных",
    )
    SSL_VERIFY: bool = Field(
        False, description="Проверка SSL-сертификатов при запросах к IntraService"
    )
    REDIS_URL: str = Field(
        "redis://localhost:6379/0", description="URL-адрес для подключения к Redis"
    )
    INTRASERVICE_TZ: str = Field(
        "Europe/Moscow", description="Часовой пояс системы IntraService"
    )
    MAX_CONCURRENT_REQUESTS: int = Field(
        10, description="Лимит одновременных подключений к IntraService"
    )
    STATUS_OPEN_ID: int = Field(
        31, description="ID статуса 'Открыта'"
    )
    STATUS_WAITING_ID: int = Field(
        35, description="ID статуса 'Требует уточнения'"
    )

    # Параметры сервисного аккаунта
    INTRASERVICE_SERVICE_LOGIN: str | None = Field(
        None, description="Логин сервисного аккаунта IntraService для фонового воркера"
    )
    INTRASERVICE_SERVICE_PASSWORD: str | None = Field(
        None, description="Пароль сервисного аккаунта IntraService для фонового воркера"
    )
    INTRASERVICE_SERVICE_USER_ID: int | None = Field(
        None, description="ID сервисного аккаунта в IntraService"
    )

    # Параметры LiteLLM и эмбеддингов
    LITELLM_API_KEY: str = Field(
        "sk-intraservice-master-key", description="API-ключ для авторизации в LiteLLM Proxy"
    )
    LITELLM_BASE_URL: str = Field(
        "http://localhost:4000/v1", description="Базовый URL для LiteLLM Proxy"
    )
    GEMINI_MODEL: str = Field(
        "gemini-2.5-flash", description="Имя LLM модели для классификации и извлечения"
    )
    EMBEDDING_MODEL: str = Field(
        "gemini-embedding-2", description="Имя модели эмбеддингов"
    )
    EMBEDDING_DIMENSION: int = Field(
        3072, description="Размерность векторов модели эмбеддингов"
    )

    AUTO_REPLY_SERVICE_IDS: list[int] = Field(
        default=[], description="ID разделов IntraService для автоматических AI-ответов"
    )
    AUTO_REPLY_MODE: str = Field(
        "comment_only", description="Режим автоответа: comment_only | comment_and_wait | comment_and_resolve"
    )

    ENCRYPTION_KEY: str | None = Field(
        None, description="Ключ для шифрования токенов в БД"
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    def __init__(self, **values):
        super().__init__(**values)
        
        # Попытка прочитать секреты из примонтированных файлов (Docker Secrets)
        if not self.INTRASERVICE_SERVICE_PASSWORD:
            if pwd := read_secret_file("INTRASERVICE_SERVICE_PASSWORD_FILE"):
                self.INTRASERVICE_SERVICE_PASSWORD = pwd

        if not self.ENCRYPTION_KEY:
            if key := read_secret_file("ENCRYPTION_KEY_FILE"):
                self.ENCRYPTION_KEY = key


settings = Settings()
