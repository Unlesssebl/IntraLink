"""Tests for Milestone 1: Data Model, Schema, Migrations, and Validation for Autopilot Internal Comments."""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from app.main import app
from app.database.db import AsyncSessionLocal, AutopilotSetting, Principal, PrincipalRole
from app.services.identity import ensure_rbac_catalog, issue_session
from app.services.ticket_runs import TicketRunService
from app.routers.ticket_runs import serialize_scenario


async def access_token(subject: str, role: str) -> str:
    async with AsyncSessionLocal() as db:
        await ensure_rbac_catalog(db, commit=False)
        principal = Principal(
            type="human",
            subject=subject,
            display_name=subject,
            status="active",
        )
        db.add(principal)
        await db.flush()
        db.add(PrincipalRole(principal_id=principal.id, role_name=role))
        await db.commit()
        token, _refresh, _expires = await issue_session(
            db,
            principal=principal,
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
        return token


@pytest.mark.asyncio
async def test_default_global_setting_has_internal_comments_defaults():
    """Проверяем, что при холодном старте выставляются дефолтные значения скрытых комментариев."""
    async with AsyncSessionLocal() as db:
        service = TicketRunService(db)
        setting = await service.get_global_setting()
        assert setting is not None
        assert setting.internal_comments_enabled is True
        assert setting.internal_comments_depth == "applied"


@pytest.mark.asyncio
async def test_update_global_setting_internal_comments():
    """Проверяем обновление параметров скрытых комментариев и валидацию глубины."""
    async with AsyncSessionLocal() as db:
        service = TicketRunService(db)
        setting = await service.get_global_setting()
        assert setting is not None
        v1 = setting.version

        # Обновляем глубину на technical и выключаем
        updated = await service.set_global_enabled(
            enabled=False,
            actor="test_admin",
            reason="Тест параметров комментариев",
            expected_version=v1,
            internal_comments_enabled=False,
            internal_comments_depth="technical",
        )
        assert updated.version == v1 + 1
        assert updated.internal_comments_enabled is False
        assert updated.internal_comments_depth == "technical"

        # Проверяем нечувствительность к регистру
        updated2 = await service.set_global_enabled(
            enabled=False,
            actor="test_admin",
            reason="Проверка регистра",
            expected_version=updated.version,
            internal_comments_enabled=True,
            internal_comments_depth="APPLIED",
        )
        assert updated2.internal_comments_enabled is True
        assert updated2.internal_comments_depth == "applied"

        # Проверяем отклонение недопустимого уровня
        with pytest.raises(ValueError, match="invalid_internal_comments_depth"):
            await service.set_global_enabled(
                enabled=False,
                actor="test_admin",
                reason="Недопустимый уровень",
                expected_version=updated2.version,
                internal_comments_depth="verbose",
            )


@pytest.mark.asyncio
async def test_database_check_constraint_rejects_invalid_depth():
    """Проверяем, что CheckConstraint базы данных блокирует недопустимые значения."""
    async with AsyncSessionLocal() as db:
        service = TicketRunService(db)
        await service.get_global_setting()

        with pytest.raises(IntegrityError):
            await db.execute(
                text("UPDATE autopilot_settings SET internal_comments_depth = 'invalid_depth' WHERE key = 'global'")
            )
            await db.commit()


@pytest.mark.asyncio
async def test_scenario_config_internal_comments_overrides():
    """Проверяем сохранение переопределений комментариев в config_json сценария."""
    async with AsyncSessionLocal() as db:
        service = TicketRunService(db)

        # Валидный конфиг с оверрайдом
        scenario = await service.upsert_scenario(
            service_id=19,
            scenario_key="install_printer",
            enabled=True,
            rollout_mode="active",
            config={
                "internal_comments_enabled": False,
                "internal_comments_depth": "technical",
            },
            actor="test_admin",
            expected_version=None,
        )
        assert scenario.config_json["internal_comments_enabled"] is False
        assert scenario.config_json["internal_comments_depth"] == "technical"

        # Проверяем сериализацию для API
        serialized = serialize_scenario(scenario)
        assert serialized["internal_comments_enabled"] is False
        assert serialized["internal_comments_depth"] == "technical"

        # Проверка валидации недопустимого типа boolean
        with pytest.raises(ValueError, match="invalid_internal_comments_enabled"):
            await service.upsert_scenario(
                service_id=19,
                scenario_key="install_printer",
                enabled=True,
                rollout_mode="active",
                config={"internal_comments_enabled": "not_a_bool"},
                actor="test_admin",
                expected_version=scenario.version,
            )

        # Проверка валидации недопустимой глубины
        with pytest.raises(ValueError, match="invalid_internal_comments_depth"):
            await service.upsert_scenario(
                service_id=19,
                scenario_key="install_printer",
                enabled=True,
                rollout_mode="active",
                config={"internal_comments_depth": "unsupported"},
                actor="test_admin",
                expected_version=scenario.version,
            )


@pytest.mark.asyncio
async def test_api_endpoints_get_and_put_settings():
    """Проверяем работу REST API эндпоинтов GET и PUT /api/v2/autopilot."""
    admin_token = await access_token("schema.admin", "system_admin")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {admin_token}"},
    ) as ac:
        # 1. GET настроек
        resp = await ac.get("/api/v2/autopilot")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "internal_comments_enabled" in data
        assert "internal_comments_depth" in data
        assert data["internal_comments_enabled"] is True
        assert data["internal_comments_depth"] == "applied"
        v = data["version"]

        # 2. PUT настроек с изменением комментариев
        put_resp = await ac.put(
            "/api/v2/autopilot",
            json={
                "enabled": False,
                "expected_version": v,
                "reason": "Тест API комментариев",
                "internal_comments_enabled": False,
                "internal_comments_depth": "technical",
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        put_data = put_resp.json()
        assert put_data["internal_comments_enabled"] is False
        assert put_data["internal_comments_depth"] == "technical"
        assert put_data["version"] == v + 1

        # 3. PUT сценария с переопределением через API
        sc_resp = await ac.put(
            "/api/v2/autopilot/scenarios",
            json={
                "service_id": 19,
                "scenario_key": "install_printer",
                "enabled": True,
                "rollout_mode": "active",
                "config": {
                    "internal_comments_enabled": True,
                    "internal_comments_depth": "applied",
                },
            },
        )
        assert sc_resp.status_code == 200, sc_resp.text
        sc_data = sc_resp.json()
        assert sc_data["internal_comments_enabled"] is True
        assert sc_data["internal_comments_depth"] == "applied"

        # 4. Отклонение некорректного Pydantic payload (422)
        bad_resp = await ac.put(
            "/api/v2/autopilot/scenarios",
            json={
                "service_id": 19,
                "scenario_key": "install_printer",
                "enabled": True,
                "rollout_mode": "active",
                "config": {
                    "internal_comments_depth": "invalid_depth",
                },
            },
        )
        assert bad_resp.status_code == 422
