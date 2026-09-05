import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.config import settings
from app.database.db import ActionPolicyRecord, AsyncSessionLocal, get_db, init_db
from app.main import app
from app.services.actions import (
    ActionRegistry,
    PolicyEngine,
    PolicyMode,
    get_action_registry,
    get_policy_engine,
)

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


@pytest_asyncio.fixture(autouse=True)
async def override_deps():
    await init_db()
    async with AsyncSessionLocal() as db:
        await db.execute(delete(ActionPolicyRecord))
        await db.commit()

    async def mock_get_db():
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.add = AsyncMock()
        yield session

    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.clear()


def test_action_registry_defaults():
    registry = get_action_registry()
    actions = registry.list_all()
    assert len(actions) >= 5

    printer_action = registry.get("install_printer")
    assert printer_action is not None
    assert printer_action.name == "Установка принтера"
    assert printer_action.category == "hardware"
    assert printer_action.default_mode == PolicyMode.CONFIRM

    diag_action = registry.get("diagnose_host")
    assert diag_action is not None
    assert diag_action.default_mode == PolicyMode.AUTO

    assert registry.get("apply_triage").default_mode == PolicyMode.CONFIRM


@pytest.mark.asyncio
async def test_policy_engine_default_and_override():
    engine = PolicyEngine()

    # 1. По умолчанию для install_printer -> CONFIRM
    policy = await engine.get_action_policy("install_printer")
    assert policy == PolicyMode.CONFIRM

    # 2. Оверрайд на DISABLED (Killswitch)
    await engine.set_action_policy("install_printer", PolicyMode.DISABLED, actor="test-admin")
    policy_disabled = await engine.get_action_policy("install_printer")
    assert policy_disabled == PolicyMode.DISABLED

    eff_mode, is_allowed, reason = await engine.evaluate_execution_mode(
        "install_printer", requested_mode="auto"
    )
    assert is_allowed is False
    assert eff_mode == "disabled"
    assert "Killswitch" in reason


@pytest.mark.asyncio
async def test_policy_cannot_escalate_confirmation_to_auto():
    engine = PolicyEngine()

    mode, allowed, _ = await engine.evaluate_execution_mode(
        "install_printer", requested_mode="auto"
    )
    assert allowed is True
    assert mode == "confirm"

    with pytest.raises(ValueError):
        await engine.set_action_policy("install_printer", PolicyMode.AUTO)


@pytest.mark.asyncio
async def test_skills_admin_api():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
            # 1. Список действий
            resp = await client.get("/api/v1/skills", headers=HEADERS)
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) >= 5
            action_ids = [a["id"] for a in data]
            assert "install_printer" in action_ids
            assert "grant_wlan" in action_ids

            # 2. Детали действия
            detail_resp = await client.get("/api/v1/skills/install_printer", headers=HEADERS)
            assert detail_resp.status_code == 200
            assert detail_resp.json()["id"] == "install_printer"

            # 3. Небезопасное действие нельзя визуально перевести в AUTO.
            patch_resp = await client.patch(
                "/api/v1/skills/install_printer/policy",
                headers=HEADERS,
                json={"mode": "auto"},
            )
            assert patch_resp.status_code == 409

            # 4. Безопасная диагностика может быть автономной.
            patch_resp = await client.patch(
                "/api/v1/skills/diagnose_host/policy",
                headers=HEADERS,
                json={"mode": "auto"},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["effective_mode"] == "auto"


@pytest.mark.asyncio
async def test_command_submit_blocked_by_killswitch():
    await PolicyEngine().set_action_policy(
        "install_printer", PolicyMode.DISABLED, actor="test-admin"
    )
    with patch("app.routers.commands.get_redis_client") as mock_cmd_redis:
        mock_r = AsyncMock()
        mock_cmd_redis.return_value = mock_r

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/commands/submit",
                headers=HEADERS,
                json={
                    "type": "install_printer",
                    "target": {"pc_name": "WS-TEST01", "printer_name": "HP LaserJet"},
                    "mode": "auto",
                },
            )
            # Должен быть заблокирован (HTTP 403 Forbidden)
            assert resp.status_code == 403
            assert "Killswitch" in resp.json()["detail"]
