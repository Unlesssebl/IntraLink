"""
Юнит-тесты для Фазы 3: Query Distillation и Advanced Hybrid RAG (Dense + Sparse RRF).
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai import DataCircuit
from app.services.rag import (
    dense_vector_search,
    distill_search_query,
    reciprocal_rank_fusion,
    search_knowledge_base,
    sparse_text_search,
)


# ===========================================================================
# 1. Тесты Query Distillation
# ===========================================================================


def test_distill_search_query_emotional_noise_removal():
    """Проверка отсечения эмоционального шума, приветствий и мольбы о помощи."""
    raw = (
        "Здравствуйте! Шеф ругается, всё пропало, срочно помогите пожалуйста! "
        "Не печатает принтер Kyocera ECOSYS M2040dn, ошибка 0x0000011b. Спасибо!"
    )
    distilled = distill_search_query(raw)
    assert "Шеф ругается" not in distilled
    assert "всё пропало" not in distilled
    assert "Здравствуйте" not in distilled
    assert "пожалуйста" not in distilled
    assert "Kyocera ECOSYS M2040dn" in distilled
    assert "0x0000011b" in distilled
    assert "Не печатает принтер" in distilled


def test_distill_search_query_preserves_technical_codes():
    """Проверка сохранения кодов ошибок, служб и моделей оборудования."""
    raw = "Добрый день. Ошибка 0x80070005 при запуске службы Spooler на хосте NTEMW0144"
    distilled = distill_search_query(raw)
    assert "0x80070005" in distilled
    assert "Spooler" in distilled
    assert "NTEMW0144" in distilled
    assert "Добрый день" not in distilled


def test_distill_search_query_fallback():
    """Проверка корректной обработки коротких и пустых запросов."""
    assert distill_search_query("") == ""
    assert distill_search_query("   ") == ""
    # Если запрос состоял только из шумных слов, но есть технический токен
    assert "0x80338029" in distill_search_query("Срочно! Помогите! 0x80338029")


# ===========================================================================
# 2. Тесты Sparse Text Search
# ===========================================================================


@pytest.mark.asyncio
async def test_sparse_text_search():
    """Проверка полнотекстового sparse-поиска по ключевым словам и кодам ошибок."""
    mock_db = AsyncMock(spec=AsyncSession)

    row1 = MagicMock()
    row1.task_id = 101
    row1.original_name = "Сбой печати на принтере HP LaserJet"
    row1.problem = "Принтер HP LaserJet выдает ошибку 0x8007007e в службе Spooler"
    row1.solution = "Перезапустить службу Spooler и очистить очередь"
    row1.service_id = 44
    row1.service_name = "03. Печать"
    row1.status_name = "Выполнена"
    row1.classification_data = {}

    row2 = MagicMock()
    row2.task_id = 102
    row2.original_name = "Настройка Wi-Fi"
    row2.problem = "Не подключается к сети WLAN-WORKNET"
    row2.solution = "Добавить пользователя в доменную группу WLAN-WORKNET"
    row2.service_id = 42
    row2.service_name = "01. Учетные записи"
    row2.status_name = "Выполнена"
    row2.classification_data = {}

    mock_res = MagicMock()
    mock_res.all.return_value = [row1, row2]
    mock_db.execute.return_value = mock_res

    results = await sparse_text_search(
        db=mock_db,
        query_text="Ошибка печати 0x8007007e Spooler",
        limit=5,
    )

    assert len(results) >= 1
    # Первая запись содержит ошибку 0x8007007e и Spooler, должна получить наивысший балл
    top = results[0]
    assert top["task_id"] == 101
    assert top["sparse_score"] > 0
    assert top["rank"] == 1


# ===========================================================================
# 3. Тесты Reciprocal Rank Fusion (RRF)
# ===========================================================================


def test_reciprocal_rank_fusion():
    """Проверка математики Reciprocal Rank Fusion (слияние Dense и Sparse)."""
    dense_results = [
        {"task_id": 101, "name": "Заявка 101", "distance": 0.15},  # rank 1
        {"task_id": 102, "name": "Заявка 102", "distance": 0.25},  # rank 2
        {"task_id": 103, "name": "Заявка 103", "distance": 0.40},  # rank 3
    ]

    sparse_results = [
        {"task_id": 102, "name": "Заявка 102", "sparse_score": 8.0},  # rank 1
        {"task_id": 104, "name": "Заявка 104", "sparse_score": 5.0},  # rank 2
        {"task_id": 101, "name": "Заявка 101", "sparse_score": 3.0},  # rank 3
    ]

    # k=60
    # doc 101: 1/(60+1) + 1/(60+3) = 1/61 + 1/63 = 0.016393 + 0.015873 = 0.032266
    # doc 102: 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522 (Победитель!)
    # doc 103: 1/(60+3) = 0.015873
    # doc 104: 1/(60+2) = 0.016129

    fused = reciprocal_rank_fusion(
        dense_results=dense_results,
        sparse_results=sparse_results,
        k=60,
        limit=3,
    )

    assert len(fused) == 3
    # Документ 102 присутствовал на высоких позициях в обоих источниках -> первое место
    assert fused[0]["task_id"] == 102
    assert fused[0]["search_type"] == "hybrid_rrf"
    assert fused[0]["dense_rank"] == 2
    assert fused[0]["sparse_rank"] == 1

    # Второе место: документ 101
    assert fused[1]["task_id"] == 101
    assert fused[1]["search_type"] == "hybrid_rrf"


# ===========================================================================
# 4. Тесты пайплайна Hybrid Search в search_knowledge_base
# ===========================================================================


@pytest.mark.asyncio
async def test_search_knowledge_base_hybrid_execution():
    """Проверка сквозного выполнения Hybrid RAG с Query Distillation."""
    mock_db = AsyncMock(spec=AsyncSession)

    dense_mock = [
        {
            "task_id": 201,
            "name": "Настройка почты",
            "problem": "Outlook не подключается",
            "solution": "Проверить настройки IMAP/SMTP",
            "service_id": 42,
            "service_name": "01. Учетные записи",
            "status_name": "Выполнена",
            "distance": 0.18,
            "rank": 1,
        }
    ]

    sparse_mock = [
        {
            "task_id": 201,
            "name": "Настройка почты",
            "problem": "Outlook не подключается",
            "solution": "Проверить настройки IMAP/SMTP",
            "service_id": 42,
            "service_name": "01. Учетные записи",
            "status_name": "Выполнена",
            "sparse_score": 6.0,
            "rank": 1,
        }
    ]

    with patch(
        "app.services.rag.dense_vector_search",
        new_callable=AsyncMock,
        return_value=dense_mock,
    ) as mock_dense, patch(
        "app.services.rag.sparse_text_search",
        new_callable=AsyncMock,
        return_value=sparse_mock,
    ) as mock_sparse:
        results = await search_knowledge_base(
            db=mock_db,
            query_text="Здравствуйте! Срочно не работает Outlook почта, помогите!",
            limit=2,
            hybrid=True,
            distill_query=True,
        )

        assert len(results) == 1
        res = results[0]
        assert res["task_id"] == 201
        assert res["search_type"] == "hybrid_rrf"
        assert "Outlook" in res["distilled_query"]
        assert "Здравствуйте" not in res["distilled_query"]

        mock_dense.assert_called_once()
        mock_sparse.assert_called_once()
