"""
Общие фикстуры pytest для тестов core-api.

Ключевая задача conftest: перехватить создание Settings ДО того как
app.config выполняет `settings = Settings()` на уровне модуля.
Это достигается через переменные окружения (os.environ), которые
pydantic-settings читает при инициализации.
"""

import os
import pytest
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Устанавливаем переменные окружения ДО любого импорта app.*
# Это гарантирует, что Settings() успешно проинициализируется без .env
# ---------------------------------------------------------------------------
os.environ.setdefault("INTRASERVICE_URL", "http://intraservice.test/api/")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("BOT_API_KEY", "test-api-key")
os.environ.setdefault("INTRASERVICE_TZ", "Europe/Moscow")
os.environ.setdefault("MAX_CONCURRENT_REQUESTS", "5")
os.environ.setdefault("POLLING_INTERVAL", "60")


@pytest.fixture
def moscow_tz() -> ZoneInfo:
    return ZoneInfo("Europe/Moscow")


@pytest.fixture
def utc_now() -> datetime:
    return datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Мок Redis-клиента с методом publish."""
    redis = AsyncMock()
    redis.publish = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def base_web_url() -> str:
    return "http://intraservice.test"
