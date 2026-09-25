"""Tests for POST /api/v2/autopilot/batch-assign endpoint."""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.core.db import get_db_session
from api.src.features.autopilot.router import get_autopilot_service_dep
from api.src.features.autopilot.service import AutopilotService
from api.src.main import app
from core.database.base import Base
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO


@pytest.fixture
async def test_db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_batch_assign_successful(test_db_session):
    mock_client = AsyncMock()

    # Ticket 101: status 1 (New) -> should be updated to status 2
    task_101 = TaskDTO(
        id=101,
        service_id=55,
        service_name="Заявка на пользователя Directum",
        name="Создание учетной записи",
        description="Создать пользователя",
        status_id=1,
        status_name="Новая",
        entities=ExtractedEntitiesDTO(),
    )
    # Ticket 102: status 2 (In work) -> status unchanged
    task_102 = TaskDTO(
        id=102,
        service_id=12,
        service_name="Оргтехника и печать",
        name="Застряла бумага",
        description="Не печатает",
        status_id=2,
        status_name="В работе",
        entities=ExtractedEntitiesDTO(pc_name="WKS-01"),
    )

    async def mock_get_task(task_id: int, **kwargs):
        if task_id == 101:
            return task_101
        elif task_id == 102:
            return task_102
        raise ValueError(f"Unknown task {task_id}")

    mock_client.get_task.side_effect = mock_get_task
    mock_client.update_task.return_value = True

    service = AutopilotService(client=mock_client)

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_autopilot_service_dep] = lambda: service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        with patch(
            "api.src.core.task_dispatch.TaskDispatchService.dispatch_autopilot_task",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            res = await client.post(
                "/api/v2/autopilot/batch-assign",
                json={"ticket_ids": [101, 102]},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["assigned_count"] == 2
            assert data["failed_ids"] == []
            assert "101" in data["details"] or 101 in data["details"]

            # Verify mock_dispatch was called for both tickets
            assert mock_dispatch.await_count == 2
            mock_dispatch.assert_any_await(101)
            mock_dispatch.assert_any_await(102)

            # Verify update_task for 101 set status_id=2
            mock_client.update_task.assert_any_await(
                task_id=101,
                status_id=2,
                comment="🤖 [Автопилот] Заявка передана на автоматическую обработку автопилоту (alen_assistant).",
                executor_ids=None,
                is_private=True,
                auth_b64=None,
            )
            # Verify update_task for 102 set status_id=None (already in work)
            mock_client.update_task.assert_any_await(
                task_id=102,
                status_id=None,
                comment="🤖 [Автопилот] Заявка передана на автоматическую обработку автопилоту (alen_assistant).",
                executor_ids=None,
                is_private=True,
                auth_b64=None,
            )

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_batch_assign_partial_failure_isolation(test_db_session):
    mock_client = AsyncMock()

    task_101 = TaskDTO(
        id=101,
        service_id=55,
        name="Создание учетки",
        description="Тест",
        status_id=1,
        status_name="Новая",
        entities=ExtractedEntitiesDTO(),
    )

    async def mock_get_task(task_id: int, **kwargs):
        if task_id == 101:
            return task_101
        raise RuntimeError("IntraService 503 Service Unavailable")

    mock_client.get_task.side_effect = mock_get_task
    mock_client.update_task.return_value = True

    service = AutopilotService(client=mock_client)

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_autopilot_service_dep] = lambda: service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        with patch(
            "api.src.core.task_dispatch.TaskDispatchService.dispatch_autopilot_task",
            new_callable=AsyncMock,
        ) as mock_dispatch:
            res = await client.post(
                "/api/v2/autopilot/batch-assign",
                json={"ticket_ids": [101, 999]},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["assigned_count"] == 1
            assert data["failed_ids"] == [999]
            assert 999 in data["details"] or "999" in data["details"]
            assert "IntraService 503" in (data["details"].get("999") or data["details"].get(999))

            assert mock_dispatch.await_count == 1
            mock_dispatch.assert_any_await(101)

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_batch_assign_validation_limits(test_db_session):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Empty list -> 422
        res_empty = await client.post("/api/v2/autopilot/batch-assign", json={"ticket_ids": []})
        assert res_empty.status_code == 422

        # More than 50 -> 422
        res_large = await client.post(
            "/api/v2/autopilot/batch-assign",
            json={"ticket_ids": list(range(1, 52))},
        )
        assert res_large.status_code == 422
