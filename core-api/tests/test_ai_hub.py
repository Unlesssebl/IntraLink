"""
Тесты для централизованного AI Hub и эндпоинтов /api/v1/ai/*.
"""
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.ai import ai_hub, TicketSummaryResult, AIAnalysisResult, ExtractedEntities


@pytest.mark.asyncio
async def test_ai_health_endpoint():
    """Проверка эндпоинта /api/v1/ai/health."""
    transport = ASGITransport(app=app)
    headers = {"X-Bot-Api-Key": "test-api-key"}

    with patch.object(ai_hub, "is_ollama_available", new=AsyncMock(return_value=True)), \
         patch.object(ai_hub, "is_litellm_available", new=AsyncMock(return_value=False)):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/ai/health", headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["ollama_available"] is True
            assert data["litellm_available"] is False


@pytest.mark.asyncio
async def test_ai_summarize_endpoint():
    """Проверка эндпоинта /api/v1/ai/summarize с моком LLM инференса."""
    transport = ASGITransport(app=app)
    headers = {"X-Bot-Api-Key": "test-api-key"}

    mock_summary = TicketSummaryResult(
        core_problem="Не печатает сетевой принтер HP",
        actions_taken=["Перезагрузка службы печати"],
        current_status="Ожидает проверки портов",
        recommended_next_step="Проверить доступность IP 10.244.12.55",
    )

    with patch.object(ai_hub, "summarize_task_history", new=AsyncMock(return_value=mock_summary)):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "task_id": 9999,
                "task_name": "Сбой печати",
                "task_desc": "Принтер в 402 кабинете не реагирует",
                "comments": [
                    {"UserName": "Иванов", "Created": "2026-09-02T10:00:00", "Text": "Попробовал выключить и включить"}
                ],
            }
            resp = await client.post("/api/v1/ai/summarize", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["core_problem"] == "Не печатает сетевой принтер HP"
            assert data["recommended_next_step"] == "Проверить доступность IP 10.244.12.55"


@pytest.mark.asyncio
async def test_ai_analyze_endpoint():
    """Проверка эндпоинта /api/v1/ai/analyze."""
    transport = ASGITransport(app=app)
    headers = {"X-Bot-Api-Key": "test-api-key"}

    mock_analysis = AIAnalysisResult(
        incident_summary="Ошибка лицензирования 1С",
        primary_category="1C_ISSUE",
        confidence=9,
        entities=ExtractedEntities(pc_names=["NTEMW0144"], target_users=["Сидоров В.В."], printer_models=[]),
        suggested_reply="Добрый день! Проверяем привязку ключа HASP на сервере 1С.",
    )

    with patch.object(ai_hub, "analyze_complex_task", new=AsyncMock(return_value=mock_analysis)):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "task_id": 8888,
                "task_name": "1С Предприятие: Не найден ключ",
                "task_desc": "На ПК NTEMW0144 сотрудника Сидоров В.В. выскакивает ошибка",
            }
            resp = await client.post("/api/v1/ai/analyze", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["primary_category"] == "1C_ISSUE"
            assert "NTEMW0144" in data["entities"]["pc_names"]
