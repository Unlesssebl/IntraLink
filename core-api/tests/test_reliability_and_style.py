from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database.db import Base, get_db, TriageAuditLog
from app.main import app
from app.services.ai_synthesis import (
    calculate_confidence_score,
    extract_thread_context,
    strip_emojis,
)

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


@pytest.fixture(autouse=True)
async def setup_test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    async def mock_get_service_auth_b64():
        return "bW9ja19hdXRoX2I2NA=="

    from app.routers.deps import get_service_auth_b64
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_service_auth_b64] = mock_get_service_auth_b64
    yield
    app.dependency_overrides.clear()


def test_strip_emojis_zero_emoji_policy():
    """Проверка полного удаления любых эмодзи и сохранения технического текста."""
    raw_text = "Здравствуйте! 🚀 По вашей заявке выполнена перезагрузка службы печати ✅. Проверьте, пожалуйста 👍."
    cleaned = strip_emojis(raw_text)
    assert cleaned == "Здравствуйте! По вашей заявке выполнена перезагрузка службы печати. Проверьте, пожалуйста."
    assert "🚀" not in cleaned
    assert "✅" not in cleaned
    assert "👍" not in cleaned


def test_confidence_score_calculation_scenarios():
    """Проверка расчета Confidence Score для различных уровней автоматизации."""
    # 1. Автоматический дубликат -> 0.99
    score_dup = calculate_confidence_score(
        rule_decision={"rule_type": "duplicate_task", "comment": "Дубликат"}
    )
    assert score_dup == 0.99

    # 2. Доменный регламент Wi-Fi / WLAN -> 0.95
    score_wlan = calculate_confidence_score(
        rule_decision={"rule_type": "wlan", "comment": "Доступ предоставлен"}
    )
    assert score_wlan == 0.95

    # 3. Высокий RAG + онлайн хост -> >= 0.85
    score_rag_online = calculate_confidence_score(
        kb_matches=[{"similarity_pct": 95, "solution": "Очистить кэш"}],
        telemetry={"ping_status": "ONLINE", "metrics": {"spooler": "Running"}},
        rule_decision={"rule_type": "1c_issue"},
    )
    assert score_rag_online >= 0.85

    # 4. Неизвестная заявка без RAG и без телеметрии хоста -> < 0.60 (требует ручной проверки)
    score_unknown = calculate_confidence_score(
        kb_matches=[],
        telemetry=None,
        rule_decision={"rule_type": "standard_in_work"},
    )
    assert score_unknown < 0.60


def test_extract_thread_context():
    """Проверка извлечения контекста переписки с отсечением системных статусов."""
    raw_history = [
        {"Comment": "Создана заявка заявителем", "UserName": "Система"},
        {"Comment": "Статус изменен на В работе", "UserName": "Система"},
        {"Comment": "Добрый день, уточните, пожалуйста, номер кабинета", "UserName": "Беликов Ален"},
        {"Comment": "Статус изменен на Требует уточнения", "UserName": "Система"},
        {"Comment": "Здравствуйте, кабинет 112, сижу у окна", "UserName": "Иванов И.И."},
    ]

    ctx = extract_thread_context(raw_history)
    assert ctx["is_follow_up"] is True
    assert ctx["last_author"] == "Иванов И.И."
    assert "кабинет 112" in ctx["last_comment"]
    # Проверяем, что системные статусы отсечены
    assert len(ctx["thread"]) == 2


@pytest.mark.asyncio
async def test_feedback_review_endpoint():
    """Проверка работы эндпоинта журнала аудита решений /feedback-review."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/triage/feedback-review?limit=5", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 0


@pytest.mark.asyncio
async def test_batch_triage_confidence_score_and_flags():
    """Проверка наличия confidence_score и флага requires_human_review в ответе /triage/batch."""
    mock_tasks = [
        {
            "Id": 139001,
            "Name": "Настроить Wi-Fi на телефоне",
            "Created": "2026-09-01T10:00:00",
            "StatusId": 26,
            "StatusName": "Новая",
            "ServiceId": 42,
            "ServiceName": "01. Учетные записи",
            "Creator": "Иванов И.И.",
            "CreatorPhone": "49-87",
            "_field_meta": {"phone": "49-87", "room": "112", "pc_name": "ZTE1234"},
        }
    ]

    with patch(
        "app.services.intraservice.get_tasks_by_filter",
        new_callable=AsyncMock,
        return_value=mock_tasks,
    ), patch(
        "app.routers.triage.get_skipped_task_ids",
        new_callable=AsyncMock,
        return_value=set(),
    ), patch(
        "app.routers.triage.search_knowledge_base",
        new_callable=AsyncMock,
        return_value=[],
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/triage/batch?limit=5", headers=HEADERS)
            assert resp.status_code == 200
            data = resp.json()
            task_item = data["tasks"][0]
            assert "confidence_score" in task_item
            assert "requires_human_review" in task_item
            assert isinstance(task_item["confidence_score"], float)
            assert isinstance(task_item["requires_human_review"], bool)

