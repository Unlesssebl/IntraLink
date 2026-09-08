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
    ), patch(
        "app.services.worker.get_redis_client",
        return_value=None,
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


@pytest.mark.asyncio
async def test_rerank_candidates_single_candidate_gate():
    """Проверка устранения Gate Bypass #1: одиночный кандидат обязан проходить оценку порогом."""
    candidate = [{
        "task_id": 601,
        "name": "Непохожая заявка",
        "problem": "Проблема А",
        "solution": "Решение А",
        "similarity_pct": 50.0,
    }]

    # 1. Если кросс-энкодер оценил ниже порога -> кандидат отсеивается (возврат [])
    with patch("app.services.rag._rerank_fastembed_sync", return_value=[0.30]):
        res_low = await rerank_candidates(
            query_text="Запрос Б",
            candidates=candidate,
            top_n=1,
            threshold=0.80,
        )
        assert res_low == []

    # 2. Если кросс-энкодер оценил выше порога -> кандидат проходит
    with patch("app.services.rag._rerank_fastembed_sync", return_value=[0.92]):
        res_high = await rerank_candidates(
            query_text="Запрос А",
            candidates=candidate,
            top_n=1,
            threshold=0.80,
        )
        assert len(res_high) == 1
        assert res_high[0]["task_id"] == 601


@pytest.mark.asyncio
async def test_rerank_candidates_all_below_threshold_returns_empty():
    """Проверка устранения Gate Bypass #2: при скорах ниже порога возвращается пустой список (no-match)."""
    candidates = [
        {"task_id": 701, "name": "Кандидат 1", "similarity_pct": 60.0},
        {"task_id": 702, "name": "Кандидат 2", "similarity_pct": 55.0},
    ]

    with patch("app.services.rag._rerank_fastembed_sync", return_value=[0.45, 0.50]):
        res = await rerank_candidates(
            query_text="Специфический запрос",
            candidates=candidates,
            top_n=2,
            threshold=0.80,
        )
        assert res == []


def test_is_valid_solution_source():
    """Проверка единого фильтра допуска источников решений."""
    from app.services.rag import is_valid_solution_source

    valid = {
        "task_id": 801,
        "status_name": "Выполнена",
        "resolution_type": "resolved",
        "solution": "Удалить обновление Windows и перезапустить службу Spooler",
    }
    assert is_valid_solution_source(valid) is True

    cancelled_status = {
        "task_id": 802,
        "status_name": "Отменена заявителем",
        "resolution_type": "resolved",
        "solution": "Удалить обновление Windows и перезапустить службу Spooler",
    }
    assert is_valid_solution_source(cancelled_status) is False

    cancelled_type = {
        "task_id": 803,
        "status_name": "Закрыта",
        "resolution_type": "cancelled",
        "solution": "Удалить обновление Windows и перезапустить службу Spooler",
    }
    assert is_valid_solution_source(cancelled_type) is False

    short_sol = {
        "task_id": 804,
        "status_name": "Выполнена",
        "resolution_type": "resolved",
        "solution": "Ок",
    }
    assert is_valid_solution_source(short_sol) is False

