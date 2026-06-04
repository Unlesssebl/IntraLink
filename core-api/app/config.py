import os
import logging
import secrets
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    INTRASERVICE_URL: str = Field(..., description="URL-адрес API IntraService")
    DATABASE_URL: str = Field("sqlite+aiosqlite:///./core_api.db", description="Строка подключения к базе данных")
    BOT_API_KEY: str = Field(None, description="Предоставленный API-ключ для авторизации бота")
    SSL_VERIFY: bool = Field(False, description="Проверка SSL-сертификатов при запросах к IntraService")

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
