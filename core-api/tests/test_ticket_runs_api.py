from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.database.db import AsyncSessionLocal, Principal, PrincipalRole, TriageTemplate
from app.main import app
from app.routers.deps import get_service_auth_b64
from app.services.identity import ensure_rbac_catalog, issue_session
from app.services.ticket_runs import REQUIRED_AUTOPILOT_TEMPLATES


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


async def seed_autopilot_templates() -> None:
    async with AsyncSessionLocal() as db:
        status_by_key = {
            "printer_ip_clarify": 35,
            "pc_offline": 35,
            "ticket_timeout_cancel": 30,
            "ticket_not_relevant": 30,
            "autopilot_unsupported_cancel": 30,
            "autopilot_execution_failed_cancel": 30,
            "resolved_standard": 29,
        }
        for key in REQUIRED_AUTOPILOT_TEMPLATES:
            existing = await db.scalar(select(TriageTemplate).where(TriageTemplate.key == key))
            if existing:
                existing.status_id = status_by_key[key]
                existing.is_active = True
                continue
            db.add(
                TriageTemplate(
                    key=key,
                    name=key,
                    category="autopilot",
                    status_id=status_by_key[key],
                    status_name="Шаблон",
                    expenses=5,
                    template_text="Шаблон",
                    is_active=True,
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_autopilot_setting_is_readable_by_operator_and_mutable_by_admin():
    await seed_autopilot_templates()
    operator = await access_token("run.operator", "helpdesk_operator")
    admin = await access_token("run.admin", "system_admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        read = await client.get(
            "/api/v2/autopilot", headers={"Authorization": f"Bearer {operator}"}
        )
        assert read.status_code == 200
        assert read.json()["enabled"] is False

        denied = await client.put(
            "/api/v2/autopilot",
            headers={"Authorization": f"Bearer {operator}"},
            json={"enabled": True, "expected_version": 1, "reason": "test change"},
        )
        assert denied.status_code == 403

        changed = await client.put(
            "/api/v2/autopilot",
            headers={"Authorization": f"Bearer {admin}"},
            json={"enabled": True, "expected_version": 1, "reason": "test change"},
        )
        assert changed.status_code == 200
        assert changed.json()["enabled"] is True
        assert changed.json()["version"] == 2


@pytest.mark.asyncio
async def test_operator_can_start_read_and_pause_manual_cycle():
    operator = await access_token("manual.operator", "helpdesk_operator")

    async def service_auth_override():
        return "service-auth"

    app.dependency_overrides[get_service_auth_b64] = service_auth_override
    try:
        with patch(
            "app.routers.ticket_runs.get_single_task",
            new=AsyncMock(return_value={"Id": 92001, "StatusId": 31, "ExecutorId": 42}),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                headers = {"Authorization": f"Bearer {operator}"}
                started = await client.post(
                    "/api/v2/ticket-runs/by-task/92001",
                    headers=headers,
                    json={"mode": "manual"},
                )
                assert started.status_code == 201
                run = started.json()
                assert run["mode"] == "manual"
                assert run["state"] == "running"

                paused = await client.post(
                    f"/api/v2/ticket-runs/{run['id']}/control",
                    headers=headers,
                    json={"action": "pause", "expected_version": run["version"]},
                )
                assert paused.status_code == 200
                assert paused.json()["state"] == "paused"

                stale = await client.post(
                    f"/api/v2/ticket-runs/{run['id']}/control",
                    headers=headers,
                    json={"action": "resume", "expected_version": run["version"]},
                )
                assert stale.status_code == 409

                view = await client.get(
                    "/api/v2/ticket-runs/by-task/92001", headers=headers
                )
                assert view.status_code == 200
                assert view.json()["run"]["state"] == "paused"
                assert len(view.json()["events"]) == 2
                assert view.json()["pending_command"] is None

                listed = await client.get(
                    "/api/v2/ticket-runs",
                    headers=headers,
                    params=[("task_ids", "92001"), ("task_ids", "99999")],
                )
                assert listed.status_code == 200
                assert [item["task_id"] for item in listed.json()["items"]] == [92001]
    finally:
        app.dependency_overrides.pop(get_service_auth_b64, None)


@pytest.mark.asyncio
async def test_direct_triage_write_is_blocked_for_active_ticket_run():
    operator = await access_token("guard.operator", "helpdesk_operator")

    async def service_auth_override():
        return "service-auth"

    app.dependency_overrides[get_service_auth_b64] = service_auth_override
    try:
        async with AsyncSessionLocal() as db:
            from app.services.ticket_runs import TicketRunService

            await TicketRunService(db).start_manual(
                task_id=92002,
                status_id=31,
                allowed_status_ids={31},
                actor="guard.operator",
                trigger_key="manual:92002:guard",
            )

        with patch(
            "app.routers.triage.TriageService.apply_triage_resolution",
            new=AsyncMock(),
        ) as apply_resolution:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/triage/apply",
                    headers={"Authorization": f"Bearer {operator}"},
                    json={
                        "task_ids": [92002],
                        "status_id": 27,
                        "comment": "Обход нового контура",
                        "expenses": 0,
                    },
                )

        assert response.status_code == 409
        assert "TicketRun" in response.json()["detail"]
        apply_resolution.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_service_auth_b64, None)


@pytest.mark.asyncio
async def test_metrics_expose_active_ticket_run_and_outbox_age():
    async with AsyncSessionLocal() as db:
        from app.services.ticket_runs import TicketRunService

        await TicketRunService(db).start_manual(
            task_id=92003,
            status_id=31,
            allowed_status_ids={31},
            actor="metrics.operator",
            trigger_key="manual:92003:metrics",
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert 'intralink_ticket_runs_active{state="running"} 1' in response.text
    assert "intralink_ticket_run_oldest_active_seconds" in response.text
    assert "intralink_command_outbox_oldest_pending_seconds" in response.text
