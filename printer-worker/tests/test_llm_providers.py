import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from orchestrator.schemas import LLMParseResult, ConnectionType

# Устанавливаем переменные окружения до импорта модулей
os.environ["BOT_API_KEY"] = "dummy_key"
os.environ["LLM_PROVIDER"] = "ollama"
os.environ["LLM_API_URL"] = "http://localhost:11434"
os.environ["LLM_MODEL_NAME"] = "llama3"

from llm import get_provider, OllamaProvider, OpenAIProvider


@pytest.mark.asyncio
async def test_get_provider_factory():
    # 1. Тест с ollama
    with patch("llm.LLM_PROVIDER", "ollama"):
        # Сбросим кешированный инстанс
        import llm

        llm._provider_instance = None

        provider = get_provider()
        assert isinstance(provider, OllamaProvider)

    # 2. Тест с openai
    with patch("llm.LLM_PROVIDER", "openai"):
        import llm

        llm._provider_instance = None

        provider = get_provider()
        assert isinstance(provider, OpenAIProvider)

    # 3. Тест с неизвестным провайдером
    with patch("llm.LLM_PROVIDER", "unknown_llm"):
        import llm

        llm._provider_instance = None

        with pytest.raises(ValueError) as exc:
            get_provider()
        assert "Unknown LLM provider" in str(exc.value)


@pytest.mark.asyncio
async def test_ollama_provider_success():
    provider = OllamaProvider()

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {
        "response": '{"target_pc": "PC-TEST", "model_key": "kyocera_ecosys_m2040dn", "connection_type": "tcpip", "printer_address": "10.244.1.2", "confidence": 0.9}'
    }

    mock_request = MagicMock()
    mock_request.__aenter__.return_value = mock_response

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_request)

    with patch("llm.ollama_provider.get_session", return_value=mock_session):
        res = await provider.parse_task_text("Установите принтер Kyocera на ПК PC-TEST")
        assert isinstance(res, LLMParseResult)
        assert res.target_pc == "PC-TEST"
        assert res.model_key == "kyocera_ecosys_m2040dn"
        assert res.connection_type == ConnectionType.TCPIP
        assert res.confidence == 0.9


@pytest.mark.asyncio
async def test_ollama_provider_fallback_on_error():
    provider = OllamaProvider()

    mock_response = AsyncMock()
    mock_response.status = 500

    mock_request = MagicMock()
    mock_request.__aenter__.return_value = mock_response
    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_request)

    with patch("llm.ollama_provider.get_session", return_value=mock_session):
        res = await provider.parse_task_text("Установите принтер")
        assert isinstance(res, LLMParseResult)
        assert res.model_key == "unknown"
        assert res.confidence == 0.0


@pytest.mark.asyncio
async def test_openai_provider_success():
    provider = OpenAIProvider()

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '{"target_pc": "PC-TEST", "model_key": "hp_lj_m428", "connection_type": "usb", "printer_address": null, "confidence": 0.85}'
                }
            }
        ]
    }

    mock_request = MagicMock()
    mock_request.__aenter__.return_value = mock_response
    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_request)

    with patch("llm.openai_provider.get_session", return_value=mock_session):
        res = await provider.parse_task_text("Install hp printer on PC-TEST via USB")
        assert isinstance(res, LLMParseResult)
        assert res.target_pc == "PC-TEST"
        assert res.model_key == "hp_lj_m428"
        assert res.connection_type == ConnectionType.USB
        assert res.confidence == 0.85


@pytest.mark.asyncio
async def test_openai_provider_fallback_on_exception():
    provider = OpenAIProvider()

    mock_session = MagicMock()
    mock_session.post = MagicMock(side_effect=Exception("OpenAI API unreachable"))

    with patch("llm.openai_provider.get_session", return_value=mock_session):
        res = await provider.parse_task_text("Install printer")
        assert isinstance(res, LLMParseResult)
        assert res.model_key == "unknown"
        assert res.confidence == 0.0
