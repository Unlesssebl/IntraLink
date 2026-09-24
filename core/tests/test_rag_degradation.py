"""Degradation and fault-tolerance tests for Universal RAG Engine.

Verifies system stability during external service outages (LiteLLM down, Redis unavailable).
"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.database.base import Base
from core.database.models import TaskKnowledgeBase
from core.intraservice.dto import TaskDTO
from core.rag.embed_cache import EmbeddingCache
from core.rag.embedder import get_embedding_vector
from core.rag.search import search_hybrid_solutions
from worker.src.scenarios.ad_password_reset import ADPasswordResetScenario
from worker.src.scenarios.install_printer import InstallPrinterScenario
from worker.src.scenarios.router import ScenarioRouter


@pytest.mark.asyncio
async def test_litellm_outage_fallback_to_fts():
    """Verify hybrid search transparently degrades to sparse FTS when LiteLLM is unreachable."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        item = TaskKnowledgeBase(
            task_id=77,
            original_name="Сброс пароля пользователя",
            problem="Пользователь заблокирован в Active Directory",
            solution="Запустить dsa.msc, найти учетную запись и установить флаг сброса пароля.",
            service_id=1,
            service_name="Учетные записи",
            status_name="Закрыта",
            embedding=None,  # No embedding available
            quality_score=1.0,
        )
        session.add(item)
        await session.commit()

        # Mock LiteLLM connection error
        mock_ai = AsyncMock()
        mock_ai.embeddings.create.side_effect = ConnectionError("LiteLLM gateway unreachable: 502 Bad Gateway")

        # Vector generation fails gracefully
        vec = await get_embedding_vector("сброс пароля пользователя", mock_ai, use_cache=False)
        assert vec is None

        # Hybrid search succeeds via Sparse Lexical channel even with query_vector=None
        results = await search_hybrid_solutions(
            session=session,
            query_text="сброс пароля пользователя",
            query_vector=None,
            limit=5,
        )

        assert len(results) == 1
        assert results[0].task_id == 77
        assert "dsa.msc" in results[0].solution

    await engine.dispose()


@pytest.mark.asyncio
async def test_redis_failure_resilience():
    """Verify EmbeddingCache continues operating in-memory if Redis raises network errors."""
    mock_redis = AsyncMock()
    mock_redis.get.side_effect = ConnectionError("Redis socket timeout")
    mock_redis.set.side_effect = ConnectionError("Redis read-only replica")

    cache = EmbeddingCache(in_memory_maxsize=10, redis_client=mock_redis)

    vec = [0.7] * 1024
    # Set must not raise exception
    await cache.set("запрос при сбое redis", vec, "bge-m3")

    # Get must hit L1 in-memory without error
    retrieved = await cache.get("запрос при сбое redis", "bge-m3")
    assert retrieved == vec


@pytest.mark.asyncio
async def test_router_operates_when_semantic_index_cold():
    """Verify router makes deterministic decisions even when semantic index warm_up failed."""
    ad_scen = ADPasswordResetScenario()
    printer_scen = InstallPrinterScenario()
    scenarios = {"ad_password_reset": ad_scen, "install_printer": printer_scen}

    # Router with failing LiteLLM
    failing_ai = AsyncMock()
    failing_ai.embeddings.create.side_effect = RuntimeError("LiteLLM offline")

    router = ScenarioRouter(ai_client=failing_ai)
    await router.warm_up()
    assert router._semantic_index.is_ready is False

    task = TaskDTO(
        Id=505,
        Name="Сброс пароля",
        Description="Забыл пароль от домена, прошу сбросить",
    )

    # Route task: must use Factor A (keyword matching) without crash
    route_res = await router.route_task(task, scenarios, threshold=0.50)
    assert route_res is not None
    matched_scenario, match_dto = route_res
    assert matched_scenario.scenario_key == "ad_password_reset"
    assert match_dto.matched is True
