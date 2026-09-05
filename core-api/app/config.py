import logging
import os
import secrets

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def read_secret_file(file_path_env: str) -> str | None:
    path = os.getenv(file_path_env)
    if path and os.path.exists(path) and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(
                "Ошибка чтения файла секрета %s (%s): %s", file_path_env, path, e
            )
    return None


class Settings(BaseSettings):
    APP_ENV: str = Field("development", description="development | test | production")
    CORS_ORIGINS: str = Field(
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",
        description="Разделённый запятыми список доверенных web origins",
    )
    INTRASERVICE_URL: str = Field(..., description="URL-адрес API IntraService")
    DATABASE_URL: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/intraservice",
        description="Строка подключения к базе данных",
    )
    BOT_API_KEY: str | None = Field(
        None, description="Предоставленный API-ключ для авторизации бота"
    )
    WORKER_API_KEY: str | None = Field(
        None, description="Отдельный ключ только для claim/finish команд исполнителями"
    )
    ALLOW_LEGACY_SHARED_KEYS: bool | None = Field(
        None, description="Временная совместимость общих BOT/WORKER ключей вне production"
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
    ENABLE_INTERNAL_SCHEDULER: bool = Field(
        False,
        description=(
            "Запуск встроенного планировщика APScheduler в процессе Core API "
            "(по умолчанию False, так как опрос выполняет отдельный контейнер poller)"
        ),
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
    STATUS_OPEN_ID: int = Field(31, description="ID статуса 'Открыта'")
    STATUS_WAITING_ID: int = Field(35, description="ID статуса 'Требует уточнения'")
    STATUS_IN_PROGRESS_ID: int = Field(27, description="ID статуса 'В работе'")
    STATUS_COMPLETED_ID: int = Field(29, description="ID статуса 'Выполнена'")
    STATUS_CANCELLED_ID: int = Field(30, description="ID статуса 'Отменена'")
    STATUS_CLOSED_ID: int = Field(28, description="ID статуса 'Закрыта'")

    # Автономный оркестратор жизненного цикла заявок
    AUTONOMOUS_LIFECYCLE_ENABLED: bool = Field(
        True, description="Включение автономного оркестратора жизненного цикла заявок"
    )
    AUTONOMOUS_AUTO_EXPENSES_MINUTES: int = Field(
        15, description="Норматив списания трудозатрат при автозакрытии"
    )
    AUTONOMOUS_TASK_LEASE_TTL: int = Field(
        120, description="TTL распределенной блокировки заявки в Redis"
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
    JWT_ISSUER: str = Field("intralink-core", description="JWT issuer")
    JWT_AUDIENCE: str = Field("intralink", description="JWT audience")
    ACCESS_TOKEN_TTL_MINUTES: int = Field(15, ge=5, le=60)
    REFRESH_SESSION_TTL_HOURS: int = Field(8, ge=1, le=168)

    # Параметры LiteLLM и эмбеддингов
    GEMINI_API_KEY: str | None = Field(
        None, description="API-ключ Google Gemini, используемый шлюзом LiteLLM"
    )
    LITELLM_API_KEY: str | None = Field(
        None,
        description="API-ключ для авторизации в LiteLLM Proxy",
    )
    LITELLM_BASE_URL: str = Field(
        "http://localhost:4000/v1", description="Базовый URL для LiteLLM Proxy"
    )
    GEMINI_MODEL: str = Field(
        "intralink-chat", description="Стабильный alias текстовой модели в LiteLLM"
    )
    EMBEDDING_MODEL: str = Field(
        "bge-m3", description="Имя модели эмбеддингов"
    )
    EMBEDDING_DIMENSION: int = Field(
        1024, description="Размерность векторов модели эмбеддингов (BGE-M3)"
    )

    # Параметры Ollama (локальный AI инференс)
    OLLAMA_BASE_URL: str = Field(
        "http://ollama:11434", description="URL-адрес для подключения к сервису Ollama"
    )
    OLLAMA_MODEL: str = Field(
        "qwen2.5:1.5b", description="Имя локальной языковой модели для суммаризации"
    )
    OLLAMA_EMBEDDING_MODEL: str = Field(
        "bge-m3", description="Имя локальной модели Ollama для генерации эмбеддингов"
    )
    OLLAMA_TIMEOUT: float = Field(
        30.0, description="Таймаут в секундах для запросов инференса Ollama"
    )
    OLLAMA_NUM_PARALLEL: int = Field(
        2, description="Лимит параллельных сессий инференса Ollama"
    )
    FASTEMBED_MODEL: str = Field(
        "BAAI/bge-m3",
        description="Имя локальной модели FastEmbed для векторных эмбеддингов",
    )
    RERANKER_MODEL: str = Field(
        "BAAI/bge-reranker-base",
        description="Имя локальной Cross-Encoder модели для реранкинга кандидатов RAG",
    )

    AUTO_REPLY_SERVICE_IDS: list[int] = Field(
        default=[], description="ID разделов IntraService для автоматических AI-ответов"
    )
    AUTO_REPLY_MODE: str = Field(
        "comment_only",
        description="Режим автоответа: comment_only | comment_and_wait | comment_and_resolve",
    )
    PRINTER_SERVICE_IDS: list[int] = Field(
        default=[],
        description="ID разделов IntraService, которые обслуживаются printer-worker'ом",
    )

    EXCLUDED_SERVICE_IDS: list[int] = Field(
        default=[173, 174, 72, 125, 136, 189, 188],
        description="ID разделов IntraService, которые глобально исключаются из системы",
    )

    DEFAULT_EXECUTOR_IDS: str = Field(
        "8664,10502",
        description="ID исполнителей по умолчанию (Беликов Ален + Беликов Ален_assitant)",
    )
    PRIMARY_EXECUTOR_ID: int = Field(
        8664,
        description="ID основного исполнителя Helpdesk для списания трудозатрат",
    )
    SKIPPED_TASKS_REDIS_TTL: int = Field(
        86400,
        description="TTL в секундах (24 часа) для кэша пропущенных заявок в Redis",
    )
    HOST_LOCK_DEFAULT_TTL: int = Field(
        30,
        description="TTL в секундах для распределенной блокировки хоста (WinRM/WMI)",
    )
    TRIAGE_APPLY_MAX_PER_MINUTE: int = Field(
        10,
        description="Порог аварийного тормоза Dead Man's Switch для применения решений (заявок/мин)",
    )
    TRIAGE_APPLY_RATE_LIMIT_WINDOW: int = Field(
        60,
        description="Окно rate limiter для применения решений в триаже (в секундах)",
    )

    # Административная панель и ролевой доступ (RBAC)
    ADMIN_LOGINS: str = Field(
        "belikov,belikov.a,IntraService_dev",
        description="Список логинов IntraService через запятую с правами администратора /admin",
    )
    ADMIN_PASSWORD: str | None = Field(
        None, description="[DEPRECATED] Устаревший мастер-пароль администратора"
    )
    ADMIN_JWT_SECRET: str | None = Field(
        None,
        description="[DEPRECATED] Используйте единый JWT_SECRET для подписи сессий",
    )
    PRIMARY_TRIAGE_FILTER_ID: int = Field(
        984, description="ID основного фильтра первой линии в IntraService"
    )
    AD_DOMAIN_NAME: str = Field(
        "corporate.loc", description="Имя домена Active Directory по умолчанию"
    )
    AD_WLAN_GROUP_NAME: str = Field(
        "WLAN-WORKNET", description="Имя доменной группы для Wi-Fi доступа"
    )

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
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
            elif self.APP_ENV.lower() == "production":
                raise ValueError("JWT_SECRET или JWT_SECRET_FILE обязателен в production")
            else:
                generated_jwt_secret = secrets.token_hex(32)
                logger.warning(
                    "JWT_SECRET не задан! Сгенерирован временный случайный ключ. "
                    "Сессии веб-панели будут сброшены при перезапуске."
                )
                self.JWT_SECRET = generated_jwt_secret

        if not self.BOT_API_KEY:
            if self.APP_ENV.lower() != "production":
                generated_key = secrets.token_hex(32)
                logger.warning(
                    "BOT_API_KEY не задан: создан временный ключ только для разработки.",
                )
                self.BOT_API_KEY = generated_key

        if not self.WORKER_API_KEY:
            self.WORKER_API_KEY = read_secret_file("WORKER_API_KEY_FILE")
        if not self.WORKER_API_KEY:
            if self.APP_ENV.lower() != "production":
                self.WORKER_API_KEY = self.BOT_API_KEY

        if self.ALLOW_LEGACY_SHARED_KEYS is None:
            self.ALLOW_LEGACY_SHARED_KEYS = self.APP_ENV.lower() != "production"
        if self.APP_ENV.lower() == "production" and self.ALLOW_LEGACY_SHARED_KEYS:
            raise ValueError("ALLOW_LEGACY_SHARED_KEYS запрещён в production")

        if not self.LITELLM_API_KEY and self.APP_ENV.lower() == "production":
            raise ValueError("LITELLM_API_KEY обязателен в production")


settings = Settings()
