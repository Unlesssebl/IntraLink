"""
Тесты для двухуровневого RAG и защиты векторизации по контурам данных (RED / YELLOW / GREEN).
"""
import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai import DataCircuit, RoutingMetadata
from app.services import rag
from app.services.ai import data_sanitizer


@pytest.mark.asyncio
async def test_get_embedding_vector_red_local_isolation():
    """Проверка, что для RED контура облачные LiteLLM / Gemini API никогда не вызываются."""
    rag._EMBED_MEMORY_CACHE.clear()
    raw_text = f"Пароль от рабочей станции NTEMW0144: SuperSecret123-{uuid.uuid4().hex}"
    dummy_vec = [0.1] * 3072

    # Мокируем локальную Ollama
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"embeddings": [dummy_vec]})
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_post = MagicMock(return_value=mock_cm)

    with patch("app.services.rag.get_rag_http_session") as mock_session_getter:
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session_getter.return_value = mock_session

        vec = await rag.get_embedding_vector(raw_text, circuit=DataCircuit.RED)
        assert vec == dummy_vec

        # Проверяем, что запрос ушел строго на локальный Ollama endpoint (/api/embed), а не в LiteLLM
        call_url = mock_post.call_args[0][0]
        assert "/api/embed" in call_url
        assert "litellm" not in call_url
        assert "generativelanguage" not in call_url


def test_security_service_metadata_forces_red_circuit():
    decision = data_sanitizer.evaluate_circuit(
        "Общий текст без явного секрета",
        RoutingMetadata(service_id=72),
    )
    assert decision.circuit == DataCircuit.RED


@pytest.mark.asyncio
async def test_get_embedding_vector_yellow_sanitization():
    """Проверка, что для YELLOW контура текст перед отправкой в облако маскируется."""
    raw_text = "Сотрудник Иванов Иван Иванович на хосте 10.244.12.55 не может войти"
    dummy_vec = [0.2] * 3072

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"data": [{"embedding": dummy_vec}]})
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_post = MagicMock(return_value=mock_cm)

    with patch("app.services.rag.get_rag_http_session") as mock_session_getter:
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session_getter.return_value = mock_session

        vec = await rag.get_embedding_vector(raw_text, circuit=DataCircuit.YELLOW)
        assert vec == dummy_vec

        # Проверяем, что в облако ушел обезличенный текст без реального IP и ФИО
        call_json = mock_post.call_args[1]["json"]
        sent_input = call_json["input"][0]
        assert "10.244.12.55" not in sent_input
        assert "Иванов Иван Иванович" not in sent_input
        assert "{{INTERNAL_IP_1}}" in sent_input
        assert "{{USER_1}}" in sent_input


@pytest.mark.asyncio
async def test_get_embedding_vector_green_direct():
    """Проверка, что для GREEN контура запрос отправляется в LiteLLM без изменений."""
    raw_text = "Как переустановить службу диспетчера очереди печати Spooler в Windows?"
    dummy_vec = [0.3] * 3072

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"data": [{"embedding": dummy_vec}]})
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_post = MagicMock(return_value=mock_cm)

    with patch("app.services.rag.get_rag_http_session") as mock_session_getter:
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session_getter.return_value = mock_session

        vec = await rag.get_embedding_vector(raw_text, circuit=DataCircuit.GREEN)
        assert vec == dummy_vec

        call_json = mock_post.call_args[1]["json"]
        sent_input = call_json["input"][0]
        assert sent_input == raw_text


@pytest.mark.asyncio
async def test_search_knowledge_base_auto_circuit():
    """Проверка автоматической оценки контура при семантическом поиске."""
    mock_db = AsyncMock(spec=AsyncSession)
    dummy_vec = [0.4] * 3072

    with patch("app.services.rag.get_embedding_vector", new=AsyncMock(return_value=dummy_vec)) as mock_get_vec:
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        # 1. Поисковый запрос с PII (должен классифицироваться как YELLOW)
        query_pii = "Не работает почта у сотрудника Петров Петр Петрович на 10.244.1.5"
        await rag.search_knowledge_base(mock_db, query_pii)

        mock_get_vec.assert_called_with(query_pii, circuit=DataCircuit.YELLOW)

        # 2. Поисковый запрос с паролем (должен классифицироваться как RED)
        query_cred = "Пользователь забыл пароль: 12345"
        await rag.search_knowledge_base(mock_db, query_cred)

        mock_get_vec.assert_called_with(query_cred, circuit=DataCircuit.RED)


@pytest.mark.asyncio
async def test_get_embedding_vector_gemini_fallback_when_litellm_fails():
    """Проверка fallback на прямой вызов Gemini API при недоступности LiteLLM."""
    raw_text = "Инструкция по настройке сканирования в сетевую папку SMB"
    dummy_vec = [0.5] * 3072

    litellm_resp = MagicMock()
    litellm_resp.status = 503

    gemini_resp = MagicMock()
    gemini_resp.status = 200
    gemini_resp.json = AsyncMock(return_value={"embedding": {"values": dummy_vec}})

    mock_cm_litellm = AsyncMock()
    mock_cm_litellm.__aenter__.return_value = litellm_resp

    mock_cm_gemini = AsyncMock()
    mock_cm_gemini.__aenter__.return_value = gemini_resp

    def post_side_effect(url, *args, **kwargs):
        if "embeddings" in url:
            return mock_cm_litellm
        elif "generativelanguage" in url:
            return mock_cm_gemini
        raise ValueError(f"Unexpected url: {url}")

    with patch("app.services.rag.get_rag_http_session") as mock_session_getter, \
         patch("app.config.settings.GEMINI_API_KEY", "test-gemini-key"):
        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=post_side_effect)
        mock_session_getter.return_value = mock_session

        vec = await rag.get_embedding_vector(raw_text, circuit=DataCircuit.GREEN)
        assert vec == dummy_vec


@pytest.mark.asyncio
async def test_get_embedding_vector_caching_prevents_duplicate_http_calls():
    """Проверка, что повторные вызовы с одинаковым текстом обслуживаются из кэша без HTTP-запросов."""
    raw_text = "Тестовый текст для проверки многоуровневого кэширования эмбеддингов 12345"
    dummy_vec = [0.42] * 3072

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"data": [{"embedding": dummy_vec}]})
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_post = MagicMock(return_value=mock_cm)

    with patch("app.services.rag.get_rag_http_session") as mock_session_getter:
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session_getter.return_value = mock_session

        # 1-й вызов: промах кэша -> должен быть 1 HTTP POST
        vec1 = await rag.get_embedding_vector(raw_text, circuit=DataCircuit.GREEN)
        assert vec1 == dummy_vec
        assert mock_post.call_count == 1

        # 2-й и 3-й вызовы: попадание в RAM кэш -> НИ ОДНОГО нового HTTP POST!
        vec2 = await rag.get_embedding_vector(raw_text, circuit=DataCircuit.GREEN)
        vec3 = await rag.get_embedding_vector(raw_text, circuit=DataCircuit.GREEN)
        assert vec2 == dummy_vec
        assert vec3 == dummy_vec
        assert mock_post.call_count == 1, "Повторные вызовы должны браться из кэша без спама в LiteLLM!"

