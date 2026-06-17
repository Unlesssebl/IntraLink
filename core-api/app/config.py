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
    INTRASERVICE_URL: str = Field(..., description="URL-адрес API IntraService")
    DATABASE_URL: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/intraservice",
        description="Строка подключения к базе данных",
    )
    BOT_API_KEY: str | None = Field(
        None, description="Предоставленный API-ключ для авторизации бота"
    )
    SSL_VERIFY: bool = Field(
        False, description="Проверка SSL-сертификатов при запросах к IntraService"
    )
    REDIS_URL: str = Field(
        "redis://localhost:6379/0", description="URL-адрес для подключения к Redis"
    )
    POLLING_INTERVAL: int = Field(
        30, description="Интервал периодического опроса в секундах"
    )
    ENCRYPTION_KEY: str | None = Field(
        None,
        description=(
            "Ключ для шифрования токенов в БД. Сгенерировать: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        ),
    )
    INTRASERVICE_TZ: str = Field(
        "Europe/Moscow", description="Часовой пояс системы IntraService"
    )
    MAX_CONCURRENT_REQUESTS: int = Field(
        10, description="Лимит одновременных подключений к IntraService"
    )
    PRINTER_PC_CUSTOM_FIELD_ID: int = Field(
        1112, description="ID кастомного поля 'Имя ПК'"
    )
    PRINTER_IP_CUSTOM_FIELD_ID: int = Field(
        1103, description="ID кастомного поля 'МФУ/IP-адрес'"
    )
    STATUS_OPEN_ID: int = Field(
        31, description="ID статуса 'Открыта'"
    )
    STATUS_WAITING_ID: int = Field(
        35, description="ID статуса 'Требует уточнения'"
    )

    # Параметры сервисного аккаунта и JWT
    INTRASERVICE_SERVICE_LOGIN: str | None = Field(
        None, description="Логин сервисного аккаунта IntraService для фонового воркера"
    )
    INTRASERVICE_SERVICE_PASSWORD: str | None = Field(
        None, description="Пароль сервисного аккаунта IntraService для фонового воркера"
    )
    INTRASERVICE_SERVICE_USER_ID: int | None = Field(
        None, description="ID сервисного аккаунта в IntraService"
    )
    JWT_SECRET: str | None = Field(
        None, description="Секрет для подписи сессионных JWT токенов администратора"
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    def __init__(self, **values):
        super().__init__(**values)
        
        # Попытка прочитать секреты из примонтированных файлов (Docker Secrets)
        if not self.ENCRYPTION_KEY:
            if key := read_secret_file("ENCRYPTION_KEY_FILE"):
                self.ENCRYPTION_KEY = key

        if not self.INTRASERVICE_SERVICE_PASSWORD:
            if pwd := read_secret_file("INTRASERVICE_SERVICE_PASSWORD_FILE"):
                self.INTRASERVICE_SERVICE_PASSWORD = pwd

        if not self.JWT_SECRET:
            if secret := read_secret_file("JWT_SECRET_FILE"):
                self.JWT_SECRET = secret
            else:
                generated_jwt_secret = secrets.token_hex(32)
                logger.warning(
                    "JWT_SECRET не задан! Сгенерирован временный случайный ключ. "
                    "Сессии веб-панели будут сброшены при перезапуске."
                )
                self.JWT_SECRET = generated_jwt_secret

        if not self.BOT_API_KEY:
            # TODO(security): В продакшене обязательно настроить BOT_API_KEY
            # в переменных окружения.
            # Для разработки сгенерируем временный ключ, чтобы сервис запустился,
            # но выдадим предупреждение.
            generated_key = secrets.token_hex(32)
            logger.warning(
                "ВНИМАНИЕ: BOT_API_KEY не задан в окружении! "
                "Сгенерирован временный случайный ключ: %s. "
                "Этот ключ будет сбрасываться при каждом перезапуске сервиса.",
                generated_key,
            )
            self.BOT_API_KEY = generated_key


settings = Settings()

