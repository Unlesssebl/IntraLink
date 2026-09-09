import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.db import Base, TaskKnowledgeBase
from app.services.rag import (
    index_task_knowledge,
    sparse_text_search,
    reciprocal_rank_fusion,
    backfill_kb_service_paths,
)


@pytest_asyncio.fixture
async def async_db_session():
    """In-memory SQLite session для изолированного тестирования логики RAG."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_index_task_knowledge_stores_hierarchy(async_db_session: AsyncSession):
    """Проверяет, что при индексации сохраняются service_path и service_path_ids."""
    with patch("app.services.rag.get_embedding_vector", new_callable=AsyncMock) as mock_emb:
        mock_emb.return_value = [0.1] * 1024

        ok = await index_task_knowledge(
            db=async_db_session,
            task_id=99001,
            original_name="Настройка сканирования",
            problem="Не работает сетевое сканирование",
            solution="Перезапустили службу WIA и переподключили сканер",
            service_id=255,
            service_name="Принтеры и МФУ",
            status_name="Выполнена",
            classification_data={"resolution_label": "Выполнено"},
            service_path="Техническое обслуживание > Оборудование > Принтеры и МФУ",
            service_path_ids=[42, 53, 255],
        )
        assert ok is True

        # Проверяем запись в базе данных
        item = await async_db_session.get(TaskKnowledgeBase, 99001)
        assert item is not None
        assert item.service_path == "Техническое обслуживание > Оборудование > Принтеры и МФУ"
        assert item.service_path_ids == [42, 53, 255]


@pytest.mark.asyncio
async def test_reciprocal_rank_fusion_service_boost():
    """Проверяет, что RRF поднимает ранги документов с совпадающим service_id."""
    dense_results = [
        {
            "task_id": 101,
            "service_id": 999,  # Другой сервис
            "service_path_ids": [10, 20],
            "quality_score": 1.0,
            "similarity_pct": 80.0,
            "rank": 1,
        },
        {
            "task_id": 102,
            "service_id": 255,  # Целевой сервис
            "service_path_ids": [42, 53, 255],
            "quality_score": 1.0,
            "similarity_pct": 79.0,
            "rank": 2,
        },
    ]
    sparse_results = [
        {
            "task_id": 101,
            "service_id": 999,
            "service_path_ids": [10, 20],
            "quality_score": 1.0,
            "sparse_score": 10.0,
            "rank": 1,
        },
        {
            "task_id": 102,
            "service_id": 255,
            "service_path_ids": [42, 53, 255],
            "quality_score": 1.0,
            "sparse_score": 9.5,
            "rank": 2,
        },
    ]

    # Без буста побеждает 101
    fused_no_boost = reciprocal_rank_fusion(
        dense_results=dense_results,
        sparse_results=sparse_results,
        limit=2,
    )
    assert fused_no_boost[0]["task_id"] == 101

    # С бустом целевого service_id=255 документ 102 должен выйти на 1 место!
    fused_boosted = reciprocal_rank_fusion(
        dense_results=dense_results,
        sparse_results=sparse_results,
        limit=2,
        service_id=255,
        service_path_ids=[42, 53, 255],
    )
    assert fused_boosted[0]["task_id"] == 102
    assert fused_boosted[0]["rrf_score"] > fused_boosted[1]["rrf_score"]


@pytest.mark.asyncio
async def test_sparse_text_search_service_boost(async_db_session: AsyncSession):
    """Проверяет токенный sparse поиск с множителем точного сервиса и ветки."""
    item1 = TaskKnowledgeBase(
        task_id=201,
        original_name="Замена картриджа Kyocera",
        problem="Бледная печать на принтере Kyocera",
        solution="Заменили тонер-картридж TK-1150",
        service_id=999,
        service_name="Прочее",
        service_path="Прочее",
        service_path_ids=[999],
        status_name="Выполнена",
        classification_data={"resolution_label": "Выполнено"},
        quality_score=1.0,
        is_blacklisted=False,
    )
    item2 = TaskKnowledgeBase(
        task_id=202,
        original_name="Замена картриджа HP",
        problem="Бледная печать на принтере HP",
        solution="Заменили тонер-картридж HP 85A",
        service_id=255,
        service_name="Принтеры и МФУ",
        service_path="Техническое обслуживание > Оборудование > Принтеры и МФУ",
        service_path_ids=[42, 53, 255],
        status_name="Выполнена",
        classification_data={"resolution_label": "Выполнено"},
        quality_score=1.0,
        is_blacklisted=False,
    )
    async_db_session.add_all([item1, item2])
    await async_db_session.commit()

    # Поиск по слову "картриджа" с приоритетом service_id=255
    matches = await sparse_text_search(
        db=async_db_session,
        query_text="картриджа",
        limit=5,
        service_id=255,
        service_path_ids=[42, 53, 255],
    )
    assert len(matches) == 2
    # item2 должен быть на первом месте благодаря x1.25 бусту
    assert matches[0]["task_id"] == 202
    assert matches[0]["service_path"] == "Техническое обслуживание > Оборудование > Принтеры и МФУ"


@pytest.mark.asyncio
async def test_backfill_kb_service_paths(async_db_session: AsyncSession):
    """Проверяет корректность фонового backfill путей для старых записей."""
    item_old = TaskKnowledgeBase(
        task_id=301,
        original_name="Сброс пароля",
        problem="Забыл пароль от домена",
        solution="Сброшен пароль в Active Directory",
        service_id=45,
        service_name="Учетные записи",
        service_path=None,  # Старая запись без пути
        service_path_ids=None,
        status_name="Выполнена",
        classification_data={"resolution_label": "Выполнено"},
        quality_score=1.0,
        is_blacklisted=False,
    )
    async_db_session.add(item_old)
    await async_db_session.commit()

    with patch("app.services.service_catalog.ServiceCatalogService.get_service_path", new_callable=AsyncMock) as mock_cat:
        mock_cat.return_value = ("ИТ > Доступ > Учетные записи", [1, 10, 45])

        updated = await backfill_kb_service_paths(async_db_session)
        assert updated == 1

        rec = await async_db_session.get(TaskKnowledgeBase, 301)
        assert rec.service_path == "ИТ > Доступ > Учетные записи"
        assert rec.service_path_ids == [1, 10, 45]


@pytest.mark.asyncio
async def test_reciprocal_rank_fusion_boost_magnitude():
    """Проверяет точный приоритетный коэффициент x1.75 для точного сервиса и x1.35 для ветки каталога."""
    dense = [{"task_id": 1, "service_id": 100, "service_path_ids": [10, 100], "quality_score": 1.0, "rank": 1}]
    sparse = [{"task_id": 1, "service_id": 100, "service_path_ids": [10, 100], "quality_score": 1.0, "sparse_score": 5.0, "rank": 1}]

    fused_plain = reciprocal_rank_fusion(dense, sparse, limit=1)
    base_score = fused_plain[0]["rrf_score"]

    fused_exact = reciprocal_rank_fusion(dense, sparse, limit=1, service_id=100)
    assert round(fused_exact[0]["rrf_score"] / base_score, 2) == 1.75

    fused_branch = reciprocal_rank_fusion(dense, sparse, limit=1, service_id=999, service_path_ids=[10])
    assert round(fused_branch[0]["rrf_score"] / base_score, 2) == 1.35

