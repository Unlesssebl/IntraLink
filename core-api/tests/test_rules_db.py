"""
Тесты для PostgreSQL SSOT моделей шаблонов, правил триажа и аудит-лога.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.db import (
    Base,
    RuleAuditLog,
    TriageRule,
    TriageTemplate,
    get_db,
)
from app.main import app
from app.services.template_engine import (
    _L1_TEMPLATES_CACHE,
    get_templates_from_db,
    invalidate_templates_cache,
    load_templates,
    seed_templates_if_empty,
)


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
async def test_seed_templates_if_empty(test_db_session: AsyncSession):
    """Проверка Seeding шаблонов в пустую БД."""
    invalidate_templates_cache()

    # 1. Первый запуск — Seeding должен заполнить таблицу
    await seed_templates_if_empty(test_db_session)

    stmt = select(TriageTemplate)
    res = await test_db_session.execute(stmt)
    templates = res.scalars().all()
    assert len(templates) > 0

    # 2. Проверка заполнения L1 кэша
    cache = await get_templates_from_db(test_db_session)
    assert len(cache) == len(templates)
    assert "in_work_standard" in cache or len(cache) > 0


@pytest.mark.asyncio
async def test_rules_admin_crud(test_db_session: AsyncSession):
    """Проверка REST эндпоинтов управления шаблонами и аудита."""
    async def override_get_db():
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    headers = {"X-Bot-Api-Key": "test-api-key"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Создание нового шаблона
        payload = {
            "key": "custom_vpn_template",
            "name": "Инструкция по подключению VPN",
            "category": "in_work",
            "status_id": 27,
            "status_name": "В работе",
            "expenses": 15,
            "template_text": "Добрый день! Для подключения к VPN используйте ПК {pc_name}.",
            "is_active": True,
        }
        resp = await client.post(
            "/api/v1/rules-admin/templates", json=payload, headers=headers
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["key"] == "custom_vpn_template"
        template_id = data["id"]

        # 2. Получение списка шаблонов
        list_resp = await client.get(
            "/api/v1/rules-admin/templates", headers=headers
        )
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert any(item["key"] == "custom_vpn_template" for item in items)

        # 3. Обновление шаблона
        update_payload = dict(payload)
        update_payload["expenses"] = 20
        put_resp = await client.put(
            f"/api/v1/rules-admin/templates/{template_id}",
            json=update_payload,
            headers=headers,
        )
        assert put_resp.status_code == 200
        assert put_resp.json()["expenses"] == 20

        # 4. Проверка записи аудит-лога
        audit_resp = await client.get(
            "/api/v1/rules-admin/audit-log", headers=headers
        )
        assert audit_resp.status_code == 200
        audit_items = audit_resp.json()
        assert len(audit_items) >= 2  # create + update

    app.dependency_overrides.clear()
