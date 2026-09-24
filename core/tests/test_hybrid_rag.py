"""Unit tests for Hybrid RAG Search (Dense + Sparse FTS) and Reciprocal Rank Fusion."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database.base import Base
from core.database.models import TaskKnowledgeBase
from core.rag.hybrid import is_valid_solution_source, reciprocal_rank_fusion
from core.rag.search import search_hybrid_solutions


def test_rrf_scoring_order():
    """Verify reciprocal rank fusion combines ranks according to weights."""
    dense_ids = [101, 102, 103]
    sparse_ids = [103, 104, 101]

    # k=60, w_dense=0.6, w_sparse=0.4
    # 101: 0.6/(60+1) + 0.4/(60+3) = 0.009836 + 0.006349 = 0.016185
    # 102: 0.6/(60+2) + 0 = 0.009677
    # 103: 0.6/(60+3) + 0.4/(60+1) = 0.009523 + 0.006557 = 0.016080
    # 104: 0 + 0.4/(60+2) = 0.006451

    results = reciprocal_rank_fusion(dense_ids, sparse_ids, k=60, weight_dense=0.6, weight_sparse=0.4)
    ranked_ids = [task_id for task_id, _ in results]

    assert ranked_ids[0] == 101  # Top fused item
    assert ranked_ids[1] == 103  # Present in both lists
    assert ranked_ids[2] == 102  # Dense only rank 2
    assert ranked_ids[3] == 104  # Sparse only rank 2


def test_solution_quality_validator():
    """Verify junk and boilerplate phrases are rejected while technical guides pass."""
    assert is_valid_solution_source("Выполнено.") is False
    assert is_valid_solution_source("Ок") is False
    assert is_valid_solution_source("Решено по телефону") is False
    assert is_valid_solution_source("Сделал, проверяйте") is False
    assert is_valid_solution_source("   ") is False

    # Valid comprehensive technical solution
    valid_solution = (
        "1. Открыть панель управления.\n"
        "2. Выбрать 'Устройства и принтеры'.\n"
        "3. Нажать 'Добавить принтер' и указать IP 192.168.1.150."
    )
    assert is_valid_solution_source(valid_solution) is True


@pytest.mark.asyncio
async def test_hybrid_search_sqlite_integration():
    """Verify search_hybrid_solutions integrates dense and sparse channels with SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        # Item 1: High semantic match for printer issue, valid technical text
        item1 = TaskKnowledgeBase(
            task_id=1,
            original_name="Принтер не печатает документы",
            problem="Очередь печати зависла, ошибка Spooler",
            solution="Перезапустить службу 'Диспетчер печати' через services.msc и очистить папку PRINTERS.",
            service_id=233,
            service_name="Принтеры",
            status_name="Закрыта",
            embedding=[0.9, 0.1, 0.0] + [0.0] * 1021,
            quality_score=1.0,
        )
        # Item 2: High lexical match for exact error code '0x80070005', valid text
        item2 = TaskKnowledgeBase(
            task_id=2,
            original_name="Сбой доступа 0x80070005 при запуске 1С",
            problem="При запуске программы 1C появляется диалог ошибки 0x80070005",
            solution="Выдать права на чтение каталога C:\\Program Files\\1cv8 пользователю Домена.",
            service_id=40,
            service_name="1С Предприятие",
            status_name="Закрыта",
            embedding=[0.0, 0.1, 0.9] + [0.0] * 1021,
            quality_score=1.0,
        )
        # Item 3: Junk boilerplate solution (must be filtered out)
        item3 = TaskKnowledgeBase(
            task_id=3,
            original_name="Ошибка 0x80070005",
            problem="Не работает 1с ошибка 0x80070005",
            solution="Выполнено.",
            service_id=40,
            service_name="1С Предприятие",
            status_name="Закрыта",
            embedding=[0.0, 0.1, 0.9] + [0.0] * 1021,
            quality_score=1.0,
        )
        session.add_all([item1, item2, item3])
        await session.commit()

        # Query with exact error code '0x80070005'
        query_text = "ошибка 0x80070005 при запуске программы"
        query_vec = [0.0, 0.1, 0.9] + [0.0] * 1021

        results = await search_hybrid_solutions(
            session=session,
            query_text=query_text,
            query_vector=query_vec,
            limit=5,
            validate_quality=True,
        )

        # Item 3 must be filtered out by is_valid_solution_source
        res_ids = [r.task_id for r in results]
        assert 3 not in res_ids
        # Item 2 must be found and ranked #1
        assert res_ids[0] == 2
        assert "Program Files" in results[0].solution

    await engine.dispose()
