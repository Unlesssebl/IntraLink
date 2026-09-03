"""
Юнит-тесты для Фазы 4: Локальный Cross-Encoder Reranker (BAAI/bge-reranker).
"""

from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag import (
    normalize_rerank_score,
    rerank_candidates,
    search_knowledge_base,
)


# ===========================================================================
# 1. Тесты нормализации скоров Cross-Encoder
# ===========================================================================


def test_normalize_rerank_score():
    """Проверка нормализации сырых скоров и логитов."""
    assert normalize_rerank_score(0.92) == 0.92
    assert normalize_rerank_score(0.0) == 0.0
    assert normalize_rerank_score(1.0) == 1.0

    # Проверка преобразования логитов через sigmoid
    score_pos = normalize_rerank_score(2.5)  # sigmoid(2.5) ~ 0.9241
    assert 0.90 <= score_pos <= 0.95

    score_neg = normalize_rerank_score(-2.5)  # sigmoid(-2.5) ~ 0.0759
    assert 0.05 <= score_neg <= 0.10


# ===========================================================================
# 2. Тесты переоценки кандидатов (Rerank)
# ===========================================================================


@pytest.mark.asyncio
async def test_rerank_candidates_reordering():
    """Проверка переупорядочивания кандидатов на основе скоров Cross-Encoder."""
    candidates = [
        {
            "task_id": 301,
            "name": "Общая проблема",
            "problem": "Принтер не отвечает",
            "solution": "Проверить кабель",
            "similarity_pct": 80.0,
        },
        {
            "task_id": 302,
            "name": "Точный прецедент",
            "problem": "Ошибка 0x0000011b на Kyocera M2040dn",
            "solution": "Удалить обновление Windows KB5005565 и добавить ключ RpcAuthnLevelExemption",
            "similarity_pct": 75.0,
        },
        {
            "task_id": 303,
            "name": "Другой принтер",
            "problem": "Замятие бумаги",
            "solution": "Очистить лоток",
            "similarity_pct": 70.0,
        },
    ]

    # Имитируем, что Cross-Encoder оценил документ 302 как наиболее релевантный (0.96)
    mock_scores = [0.40, 0.96, 0.35]

    with patch(
        "app.services.rag._rerank_fastembed_sync", return_value=mock_scores
    ):
        reranked = await rerank_candidates(
            query_text="Ошибка 0x0000011b Kyocera",
            candidates=candidates,
            top_n=2,
            threshold=0.80,
        )

        assert len(reranked) == 1
        assert reranked[0]["task_id"] == 302
        assert reranked[0]["rerank_score"] == 0.96
        assert reranked[0]["similarity_pct"] == 96.0
        assert reranked[0]["search_type"] == "hybrid_reranked"


@pytest.mark.asyncio
async def test_rerank_candidates_fallback_graceful():
    """Проверка прозрачного fallback при отсутствии библиотеки или сбое модели."""
    candidates = [
        {"task_id": 401, "name": "Заявка 1", "similarity_pct": 88.0},
        {"task_id": 402, "name": "Заявка 2", "similarity_pct": 82.0},
    ]

    with patch("app.services.rag._rerank_fastembed_sync", return_value=None):
        results = await rerank_candidates(
            query_text="Тестовый запрос",
            candidates=candidates,
            top_n=2,
        )

        assert len(results) == 2
        assert results[0]["task_id"] == 401
        assert results[0]["rerank_fallback"] is True


# ===========================================================================
# 3. Интеграционный тест search_knowledge_base с Reranker
# ===========================================================================


@pytest.mark.asyncio
async def test_search_knowledge_base_with_reranker():
    """Проверка работы полного двухэтапного пайплайна RAG: Hybrid RRF -> Cross-Encoder."""
    mock_db = AsyncMock(spec=AsyncSession)

    dense_mock = [
        {"task_id": 501, "name": "Решение 501", "distance": 0.20, "rank": 1},
        {"task_id": 502, "name": "Решение 502", "distance": 0.25, "rank": 2},
    ]
    sparse_mock = [
        {
            "task_id": 502,
            "name": "Решение 502",
            "sparse_score": 10.0,
            "rank": 1,
        },
        {"task_id": 501, "name": "Решение 501", "sparse_score": 5.0, "rank": 2},
    ]

    with patch(
        "app.services.rag.dense_vector_search",
        new_callable=AsyncMock,
        return_value=dense_mock,
    ), patch(
        "app.services.rag.sparse_text_search",
        new_callable=AsyncMock,
        return_value=sparse_mock,
    ), patch(
        "app.services.rag._rerank_fastembed_sync",
        return_value=[0.95, 0.88],
    ):
        results = await search_knowledge_base(
            db=mock_db,
            query_text="Ошибка подключения к принтеру",
            limit=2,
            hybrid=True,
            rerank=True,
            rerank_threshold=0.80,
        )

        assert len(results) == 2
        assert results[0]["rerank_score"] >= 0.80
        assert results[0]["search_type"] == "hybrid_reranked"
