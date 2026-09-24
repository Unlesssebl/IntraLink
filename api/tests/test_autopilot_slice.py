"""API Integration tests for Autopilot governance slice."""

from typing import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.core.db import get_db_session
from api.src.features.autopilot.router import get_policy_service_dep
from api.src.main import app
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.base import Base


class MockRedis:
    def __init__(self) -> None:
        self.store = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0):
        self.store[key] = value
        return True


@pytest.fixture
async def test_db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def mock_redis() -> MockRedis:
    return MockRedis()


@pytest.fixture
def override_policy_service(mock_redis) -> AutopilotPolicyService:
    return AutopilotPolicyService(redis_client=mock_redis)


@pytest.mark.asyncio
async def test_get_autopilot_policies_endpoint(test_db_session, override_policy_service):
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/v2/autopilot/policies")
        assert res.status_code == 200
        data = res.json()
        assert "policies" in data
        assert data["total"] >= 3
        keys = [p["scenario_key"] for p in data["policies"]]
        assert "install_printer" in keys
        assert "ad_password_reset" in keys
        assert "rag_consultation" in keys

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_put_autopilot_policy_endpoint(test_db_session, override_policy_service):
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Update install_printer to FULL_AUTO
        payload = {"mode": "FULL_AUTO", "min_confidence": 0.95}
        put_res = await client.put("/api/v2/autopilot/policies/install_printer", json=payload)
        assert put_res.status_code == 200
        data = put_res.json()
        assert data["scenario_key"] == "install_printer"
        assert data["mode"] == "FULL_AUTO"
        assert data["min_confidence"] == 0.95
        assert not data["is_circuit_broken"]

        # 2. Invalid mode error check
        bad_res = await client.put(
            "/api/v2/autopilot/policies/install_printer",
            json={"mode": "INVALID_MODE"},
        )
        assert bad_res.status_code == 422

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reset_circuit_breaker_endpoint(test_db_session, override_policy_service):
    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    # Simulate 3 failures
    for _ in range(3):
        await override_policy_service.record_failure("install_printer", session=test_db_session)

    policy_broken = await override_policy_service.get_policy("install_printer", session=test_db_session)
    assert policy_broken.is_circuit_broken is True

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post("/api/v2/autopilot/policies/install_printer/reset")
        assert res.status_code == 200
        data = res.json()
        assert data["is_circuit_broken"] is False
        assert data["consecutive_failures"] == 0

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_commands_and_stats_endpoints(test_db_session, override_policy_service):
    from core.database.models import CommandRecord

    async def override_db():
        yield test_db_session

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_policy_service_dep] = lambda: override_policy_service

    # Insert a dummy CommandRecord
    cmd = CommandRecord(
        idempotency_key="cmd_test_1",
        action="install_printer",
        executor="worker",
        status="succeeded",
        initiator="test",
        task_id=101,
    )
    test_db_session.add(cmd)
    await test_db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Commands feed
        feed_res = await client.get("/api/v2/autopilot/commands")
        assert feed_res.status_code == 200
        commands = feed_res.json()
        assert len(commands) >= 1
        assert commands[0]["action"] == "install_printer"
        assert commands[0]["status"] == "succeeded"

        # Stats
        stats_res = await client.get("/api/v2/autopilot/stats")
        assert stats_res.status_code == 200
        stats = stats_res.json()
        assert stats["total_automated_actions"] >= 1
        assert stats["hours_saved"] >= 0.2
        assert stats["active_scenarios_count"] >= 3

    app.dependency_overrides.clear()

