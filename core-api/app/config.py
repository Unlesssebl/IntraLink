import os
import logging
import secrets
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    INTRASERVICE_URL: str = Field(..., description="URL-адрес API IntraService")
    DATABASE_URL: str = Field("postgresql+asyncpg://postgres:postgres@localhost:5432/intraservice", description="Строка подключения к базе данных")
    BOT_API_KEY: Optional[str] = Field(None, description="Предоставленный API-ключ для авторизации бота")
    SSL_VERIFY: bool = Field(False, description="Проверка SSL-сертификатов при запросах к IntraService")
    REDIS_URL: str = Field("redis://localhost:6379/0", description="URL-адрес для подключения к Redis")
    POLLING_INTERVAL: int = Field(60, description="Интервал периодического опроса в секундах")
    ENCRYPTION_KEY: Optional[str] = Field(None, description="Ключ для шифрования токенов в БД. Сгенерировать: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def __init__(self, **values):
        super().__init__(**values)
        if not self.BOT_API_KEY:
            # TODO(security): В продакшене обязательно настроить BOT_API_KEY в переменных окружения.
            # Для разработки сгенерируем временный ключ, чтобы сервис запустился, но выдадим предупреждение.
            generated_key = secrets.token_hex(32)
            logger.warning(
                "ВНИМАНИЕ: BOT_API_KEY не задан в окружении! "
                "Сгенерирован временный случайный ключ: %s. "
                "Этот ключ будет сбрасываться при каждом перезапуске сервиса.",
                generated_key
            )
            self.BOT_API_KEY = generated_key

settings = Settings()
