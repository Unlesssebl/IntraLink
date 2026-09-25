"""IntraLink API configuration settings (Pydantic Settings v2)."""

from typing import List, Optional, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # System & Environment
    APP_NAME: str = "IntraLink API"
    APP_VERSION: str = "2.0.0"
    APP_ENV: str = "development"
    DEBUG: bool = False
    API_PORT: int = 8000

    # PostgreSQL + pgvector
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/intraservice"

    # Redis Cache & Queue
    REDIS_URL: str = "redis://localhost:6379/0"

    # LiteLLM AI Gateway
    LITELLM_BASE_URL: str = "http://localhost:4000/v1"
    LITELLM_API_KEY: str = "sk-intralink-dev"
    LITELLM_MODEL_FAST: str = "helpdesk-fast"
    LITELLM_MODEL_REASONING: str = "helpdesk-reasoning"

    # IntraService Integration
    # INVARIANT (GEMINI.md): URL must end with /api (without trailing slash)
    INTRASERVICE_URL: str = "https://servicedesk-pub.corporate.loc/api"
    INTRASERVICE_TZ: str = "Europe/Moscow"
    INTRASERVICE_LOGIN: str = ""
    INTRASERVICE_PASSWORD: str = ""
    SSL_VERIFY: bool = False
    BOT_USER_ID: Optional[int] = None

    # Security & CORS
    CORS_ORIGINS: Union[List[str], str] = ["http://localhost:3000", "http://localhost:5173"]
    JWT_SECRET: str = "dev-secret-key-change-in-production-must-be-long"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12

    model_config = SettingsConfigDict(
        env_file=(".env", "deploy/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    @field_validator("INTRASERVICE_URL")
    @classmethod
    def normalize_intraservice_url(cls, v: str) -> str:
        # Strip trailing slash to keep consistent: https://host/api
        return v.rstrip("/")


settings = Settings()
