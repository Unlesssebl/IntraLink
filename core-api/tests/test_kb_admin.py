import json
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database.db import Base, TaskKnowledgeBase, get_db
from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def test_db_session():
    """Тестовая in-memory база данных SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_kb_admin_auth_required():
    """Проверка требования Bearer токена администратора."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/admin/kb/examples")
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_kb_admin_examples_and_blacklisting(test_db_session: AsyncSession):
    """Проверка чтения прецедентов, поиска и занесения в черный список."""
    async def override_get_db():
        yield test_db_session
    app.dependency_overrides[get_db] = override_get_db

    # Создаем тестовые записи базы знаний
    item1 = TaskKnowledgeBase(
        task_id=101,
        original_name="Не печатает принтер HP",
        problem="Замятие бумаги в лотке 2",
        solution="Извлечен замятый лист бумаги, перезапущен Spooler",
        service_id=20,
        service_name="Принтеры",
        status_name="Выполнена",
        classification_data={},
        is_blacklisted=False,
    )
    item2 = TaskKnowledgeBase(
        task_id=102,
        original_name="Ошибка 1C",
        problem="Ошибка формата потока при входе в 1С",
        solution="Очищен кэш пользователя в AppData/Local/1C",
        service_id=10,
        service_name="1С:Предприятие",
        status_name="Выполнена",
        classification_data={},
        is_blacklisted=False,
    )
    test_db_session.add_all([item1, item2])
    await test_db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Логинимся для получения токена
        login_res = await client.post(
            "/api/v1/admin/auth/login",
            json={"password": settings.ADMIN_PASSWORD},
        )
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Получаем список примеров
        res_list = await client.get("/api/v1/admin/kb/examples", headers=headers)
        assert res_list.status_code == 200
        data = res_list.json()
        assert data["total"] == 2
        assert len(data["examples"]) == 2

        # 2. Поиск по слову "1С"
        res_search = await client.get("/api/v1/admin/kb/examples?search=1С", headers=headers)
        assert res_search.status_code == 200
        assert res_search.json()["total"] == 1
        assert res_search.json()["examples"][0]["task_id"] == 102

        # 3. Добавление в черный список задачи 101
        res_del = await client.delete("/api/v1/admin/kb/examples/101", headers=headers)
        assert res_del.status_code == 200
        assert res_del.json()["status"] == "success"

        # 4. Проверяем, что задача 101 исчезла из активных
        res_after = await client.get("/api/v1/admin/kb/examples", headers=headers)
        assert res_after.status_code == 200
        assert res_after.json()["total"] == 1
        assert res_after.json()["examples"][0]["task_id"] == 102

        # 5. Проверяем статистику покрытия
        res_stats = await client.get("/api/v1/admin/kb/stats", headers=headers)
        assert res_stats.status_code == 200
        stats_data = res_stats.json()
        assert stats_data["total_active_examples"] == 1
        assert stats_data["total_blacklisted_examples"] == 1


@pytest.mark.asyncio
async def test_kb_admin_services_tree():
    """Проверка сборки дерева услуг из кэша Redis."""
    mock_catalog = [
        {"id": 1, "name": "Оборудование", "parent_id": None},
        {"id": 2, "name": "Принтеры", "parent_id": 1},
        {"id": 3, "name": "Мониторы", "parent_id": 1},
    ]

    mock_redis = AsyncMock()
    mock_redis.get.return_value = json.dumps(mock_catalog)

    with patch("app.routers.kb_admin.get_redis_client", return_value=mock_redis):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login_res = await client.post(
                "/api/v1/admin/auth/login",
                json={"password": settings.ADMIN_PASSWORD},
            )
            token = login_res.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            res = await client.get("/api/v1/admin/kb/services-tree", headers=headers)
            assert res.status_code == 200
            tree = res.json()
            assert len(tree) == 1
            assert tree[0]["name"] == "Оборудование"
            assert len(tree[0]["children"]) == 2
