"""API-level E2E coverage for the operator's safe AI suggestion workflow."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database.db import get_db
from app.main import app
from app.routers.deps import get_service_auth_b64

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


class MemoryRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.xadd = AsyncMock(return_value="1-0")
        self.publish = AsyncMock(return_value=1)
        self.lpush = AsyncMock(return_value=1)

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **_kwargs):
        if _kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    async def eval(self, _script, _number_of_keys, key, owner):
        if self.values.get(key) != owner:
            return 0
        return await self.delete(key)


@pytest.fixture(autouse=True)
def override_dependencies():
    async def mock_get_service_auth_b64():
        return "test-auth"

    async def mock_get_db():
        session = AsyncMock()
        session.add = MagicMock()
        session.scalar = AsyncMock(return_value=None)
        scalar_result = MagicMock()
        scalar_result.all.return_value = []
        session.scalars = AsyncMock(return_value=scalar_result)
        yield session

    app.dependency_overrides[get_service_auth_b64] = mock_get_service_auth_b64
    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.clear()


def card():
    return {
        "task": {
            "Id": 701,
            "Name": "Выдать Wi-Fi",
            "Description": "Нужен доступ",
            "StatusId": 26,
            "CreatorLogin": "operator.test",
        },
        "history": [{"Id": 1, "Comment": "Создана"}],
        "suggested_action": {"rule_type": "wlan_access", "status_id": 29},
        "ai_suggested_resolution": "Доступ будет предоставлен после подтверждения.",
    }


@pytest.mark.asyncio
async def test_analysis_lease_prevents_duplicate_run():
    from app.routers.triage import acquire_analysis_lease, release_analysis_lease

    redis = MemoryRedis()
    with patch("app.routers.triage.get_redis_client", return_value=redis):
        client, key, owner, fence = await acquire_analysis_lease(701)
        assert fence == 1
        with pytest.raises(HTTPException) as exc:
            await acquire_analysis_lease(701)
        assert exc.value.detail == "analysis_already_running"
        await release_analysis_lease(client, key, owner)


@pytest.mark.asyncio
async def test_expired_owner_cannot_release_new_analysis_lease():
    from app.routers.triage import acquire_analysis_lease, release_analysis_lease

    redis = MemoryRedis()
    with patch("app.routers.triage.get_redis_client", return_value=redis):
        client, key, old_owner, _ = await acquire_analysis_lease(701)
        redis.values.pop(key)
        _, _, new_owner, _ = await acquire_analysis_lease(701)

        await release_analysis_lease(client, key, old_owner)

        assert redis.values[key] == new_owner


@pytest.mark.asyncio
async def test_ticket_change_during_analysis_never_finalizes_result():
    from app.routers.triage import run_explicit_analysis

    redis = MemoryRedis()
    initial = {"task": card()["task"], "history": card()["history"]}
    changed = {
        "task": {**card()["task"], "Description": "Пользователь уточнил заявку"},
        "history": card()["history"],
    }
    db = AsyncMock()
    db.add = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    with (
        patch("app.routers.triage.get_redis_client", return_value=redis),
        patch(
            "app.routers.triage.TriageService.get_task_card_snapshot",
            new_callable=AsyncMock,
            side_effect=[initial, changed],
        ),
        patch(
            "app.routers.triage.TriageService.get_task_card_details",
            new_callable=AsyncMock,
            return_value=card(),
        ),
        patch(
            "app.routers.triage.attach_durable_decision",
            new_callable=AsyncMock,
        ) as finalize,
    ):
        with pytest.raises(HTTPException) as exc:
            await run_explicit_analysis(
                task_id=701,
                service_auth_b64="test-auth",
                actor="operator:test",
                db=db,
                force=True,
            )
        assert exc.value.detail == "ticket_changed_during_analysis"
        finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_opening_inspector_is_read_only_and_reports_not_analyzed():
    snapshot = {"task": card()["task"], "history": card()["history"]}
    with (
        patch(
            "app.routers.triage.TriageService.get_task_card_snapshot",
            new_callable=AsyncMock,
            return_value=snapshot,
        ),
        patch(
            "app.routers.triage.TriageService.get_task_card_details",
            new_callable=AsyncMock,
        ) as compute,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            proposal = await client.get("/api/v1/triage/tasks/701", headers=HEADERS)
            assert proposal.status_code == 200
            analysis = proposal.json()["analysis"]
            assert analysis["has_result"] is False
            assert analysis["state"] == "not_analyzed"
            compute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsafe_auto_is_demoted_and_nothing_executes_before_confirm_e2e():
    redis = MemoryRedis()
    # This is the current proposal that the web UI binds to the command.
    redis.values["ai:suggestion:702"] = json.dumps(
        {
            "task_id": 702,
            "state": "current",
            "fingerprint": "current-version",
            "policy": {"blocked": False},
            "missing_data": [],
        }
    )
    with patch("app.routers.commands.get_redis_client", return_value=redis):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            queued = await client.post(
                "/api/v1/commands",
                headers=HEADERS,
                json={
                    "type": "grant_wlan",
                    "target": {"task_id": 702},
                    "params": {"username": "operator.test"},
                    "mode": "auto",
                    "source": "web",
                    "suggestion_task_id": 702,
                    "suggestion_fingerprint": "current-version",
                },
            )
            assert queued.status_code == 202
            job_id = queued.json()["job_id"]
            assert queued.json()["mode"] == "confirm"
            stream_fields = redis.xadd.await_args.args[1]
            assert stream_fields["mode"] == "confirm"
            redis.lpush.assert_not_awaited()  # no approval, hence no execution release

            await client.post(
                f"/api/v1/commands/{job_id}/confirm",
                headers=HEADERS,
                json={"decision": "approve"},
            )
            redis.lpush.assert_awaited_once()
