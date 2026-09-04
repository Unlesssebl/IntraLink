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
async def test_ai_generate_routed_endpoint():
    """Проверка эндпоинта /api/v1/ai/generate с автоматической маршрутизацией."""
    transport = ASGITransport(app=app)
    headers = {"X-Bot-Api-Key": "test-api-key"}

    # 1. Тест RED контура (пароли)
    with patch.object(ai_hub, "generate_ollama_completion", new=AsyncMock(return_value="Локальный ответ для сброса пароля")), \
         patch("app.services.ai.sanitizer.get_redis_client") as mock_redis:
        mock_redis.return_value = AsyncMock(get=AsyncMock(return_value=None), setex=AsyncMock(return_value=True))

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "prompt": "Сбрось пароль для пользователя. Временный пароль: SecretPass123",
                "metadata": {"contains_credentials": True},
            }
            resp = await client.post("/api/v1/ai/generate", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["circuit"] == "red"
            assert data["text"] == "Локальный ответ для сброса пароля"

    # 2. Тест YELLOW контура (маскирование PII -> Gemini -> деанонимизация)
    with patch.object(ai_hub, "generate_cloud_completion", new=AsyncMock(return_value="Инструкция для {{USER_1}} по подключению к {{INTERNAL_IP_1}}")), \
         patch("app.services.ai.sanitizer.get_redis_client") as mock_redis:
        mock_redis.return_value = AsyncMock(get=AsyncMock(return_value=None), setex=AsyncMock(return_value=True))

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "prompt": "Подключить сотрудника Иванов Иван Иванович к хосту 10.244.12.55",
            }
            resp = await client.post("/api/v1/ai/generate", json=payload, headers=headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["circuit"] == "yellow"
            assert data["sanitized_entities_count"] >= 2
            # Проверяем, что в финальном ответе плейсхолдеры были деанонимизированы
            assert "Иванов Иван Иванович" in data["text"]
            assert "10.244.12.55" in data["text"]


@pytest.mark.asyncio
async def test_ai_sanitize_preview_and_deanonymize():
    """Проверка эндпоинтов /api/v1/ai/sanitize-preview и /api/v1/ai/deanonymize."""
    transport = ASGITransport(app=app)
    headers = {"X-Bot-Api-Key": "test-api-key"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Preview
        preview_payload = {
            "text": "Проверить рабочую станцию NTEMW0144 сотрудника Петров Петр Петрович",
            "metadata": {},
        }
        resp = await client.post("/api/v1/ai/sanitize-preview", json=preview_payload, headers=headers)
        assert resp.status_code == 200
        preview_data = resp.json()
        assert preview_data["route_decision"]["circuit"] == "yellow"
        assert "{{HOST_1}}" in preview_data["sanitized_text"]
        assert "{{USER_1}}" in preview_data["sanitized_text"]

        # Deanonymize
        deanon_payload = {
            "text": "Готово для {{USER_1}} на хосте {{HOST_1}}",
            "entity_map": preview_data["entity_map"],
        }
        resp_deanon = await client.post("/api/v1/ai/deanonymize", json=deanon_payload, headers=headers)
        assert resp_deanon.status_code == 200
        assert "Петров Петр Петрович" in resp_deanon.json()["restored_text"]
        assert "NTEMW0144" in resp_deanon.json()["restored_text"]


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


@pytest.mark.asyncio
async def test_ai_routed_fallback_to_ollama():
    """Проверка fallback на локальную Ollama при отказе Cloud Gemini."""
    from app.services.ai.schemas import RoutedInferenceRequest, RoutingMetadata

    req = RoutedInferenceRequest(
        prompt="Как настроить принтер на ПК NTEMW001?",
        metadata=RoutingMetadata(),
        bypass_cache=True,
    )

    # Моделируем сбой Cloud LiteLLM (return None) и успешный ответ Ollama
    with patch.object(ai_hub, "generate_cloud_completion", new=AsyncMock(return_value=None)), \
         patch.object(ai_hub, "generate_ollama_completion", new=AsyncMock(return_value="Локальный fallback ответ")), \
         patch("app.services.ai.sanitizer.get_redis_client") as mock_redis, \
         patch("app.services.ai.hub.get_redis_client") as mock_redis_hub:

        mock_redis.return_value = AsyncMock(set=AsyncMock(return_value=True), get=AsyncMock(return_value=None))
        mock_redis_hub.return_value = AsyncMock(set=AsyncMock(return_value=True), get=AsyncMock(return_value=None))

        resp = await ai_hub.dispatch_routed_inference(req)
        assert resp is not None
        assert "fallback" in resp.model
        assert resp.text == "Локальный fallback ответ"


@pytest.mark.asyncio
async def test_ai_routed_cache_hit():
    """Проверка отдачи ответа из L2 Redis кэша."""
    from app.services.ai.schemas import RoutedInferenceRequest, RoutingMetadata

    req = RoutedInferenceRequest(
        prompt="Повторный запрос",
        metadata=RoutingMetadata(),
        bypass_cache=False,
    )

    cached_json = '{"text": "Закэшированный ответ", "circuit": "green", "model": "gemini", "sanitized_entities_count": 0, "execution_time_ms": 10.0, "cached": false}'

    with patch("app.services.ai.hub.get_redis_client") as mock_redis_hub:
        mock_redis_hub.return_value = AsyncMock(get=AsyncMock(return_value=cached_json))

        resp = await ai_hub.dispatch_routed_inference(req)
        assert resp is not None
        assert resp.cached is True
        assert resp.text == "Закэшированный ответ"


@pytest.mark.asyncio
async def test_ai_dlp_failure_blocks_cloud_inference():
    """Ошибка классификатора не имеет права перейти в GREEN/cloud fallback."""
    from app.services.ai.schemas import RoutedInferenceRequest, RoutingMetadata

    request = RoutedInferenceRequest(prompt="Any text", metadata=RoutingMetadata(), bypass_cache=True)
    with patch("app.services.ai.hub.data_sanitizer.sanitize", side_effect=RuntimeError("DLP unavailable")), \
         patch("app.services.ai.hub.record_security_event", new=AsyncMock()) as audit, \
         patch.object(ai_hub, "generate_cloud_completion", new=AsyncMock()) as cloud:
        assert await ai_hub.dispatch_routed_inference(request) is None
        cloud.assert_not_awaited()
        audit.assert_awaited_once()

