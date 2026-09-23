"""Integration tests for Knowledge Base feature slice."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.src.core.db import get_db_session
from api.src.main import app
from core.rag.search import KnowledgeSolutionDTO


@pytest.mark.asyncio
async def test_kb_ask_endpoint():
    async def override_db():
        session = AsyncMock()
        yield session

    app.dependency_overrides[get_db_session] = override_db

    transport = ASGITransport(app=app)
    mock_solution = KnowledgeSolutionDTO(
        task_id=555,
        original_name="Зависает Directum",
        problem="Клиент Directum падает при старте",
        solution="Очистить кэш в %LOCALAPPDATA%\\DIRECTUM",
        service_id=234,
        service_name="05. DIRECTUM / Технические проблемы",
        status_name="Закрыта",
        quality_score=1.0,
        similarity=0.88,
        classification_data={},
    )

    with (
        patch("api.src.features.knowledge_base.service.get_embedding_vector", new_callable=AsyncMock) as mock_embed,
        patch(
            "api.src.features.knowledge_base.service.search_similar_solutions", new_callable=AsyncMock
        ) as mock_search,
        patch("openai.resources.chat.completions.AsyncCompletions.create", new_callable=AsyncMock) as mock_chat,
    ):
        mock_embed.return_value = [0.1] * 1024
        mock_search.return_value = [mock_solution]

        mock_choice = AsyncMock()
        mock_choice.message.content = "Для решения проблемы очистите кэш согласно [Заявка #555]."
        mock_resp = AsyncMock()
        mock_resp.choices = [mock_choice]
        mock_chat.return_value = mock_resp

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v2/kb/ask",
                json={"query": "Directum не открывается и вылетает"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert 555 in data["cited_tasks"]
            assert "Заявка #555" in data["answer"]
            assert data["confidence"] > 0.8

    app.dependency_overrides.clear()
