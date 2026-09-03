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

    # Параметры LiteLLM и эмбеддингов
    GEMINI_API_KEY: str | None = Field(
        None, description="API-ключ Google Gemini API для прямого доступа без прокси"
    )
    GEMINI_API_KEY_2: str | None = Field(
        None, description="Резервный API-ключ Google Gemini API #2"
    )
    GEMINI_API_KEY_3: str | None = Field(
        None, description="Резервный API-ключ Google Gemini API #3"
    )
    GROQ_API_KEY: str | None = Field(
        None, description="API-ключ Groq для быстрого облачного инференса Llama-3.3"
    )
    OPENROUTER_API_KEY: str | None = Field(
        None, description="API-ключ OpenRouter"
    )
    LITELLM_API_KEY: str = Field(
        "sk-intraservice-master-key",
        description="API-ключ для авторизации в LiteLLM Proxy",
    )
    LITELLM_BASE_URL: str = Field(
        "http://localhost:4000/v1", description="Базовый URL для LiteLLM Proxy"
    )
    GEMINI_MODEL: str = Field(
        "gemini-3.5-flash", description="Имя LLM модели для классификации и извлечения"
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
    ADMIN_JWT_SECRET: str = Field(
        "intralink-admin-jwt-secret-key-32chars!",
        description="Секретный ключ для подписи сессионных JWT токенов администратора",
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
