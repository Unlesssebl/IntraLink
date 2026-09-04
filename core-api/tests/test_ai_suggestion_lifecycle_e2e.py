"""API-level E2E coverage for the operator's safe AI suggestion workflow."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database.db import get_db
from app.main import app
from app.routers.deps import get_service_auth_b64

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


class MemoryRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.xadd = AsyncMock(return_value="1-0")
        self.publish = AsyncMock(return_value=1)
        self.lpush = AsyncMock(return_value=1)

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **_kwargs):
        self.values[key] = value
        return True


@pytest.fixture(autouse=True)
def override_dependencies():
    async def mock_get_service_auth_b64():
        return "test-auth"

    async def mock_get_db():
        session = AsyncMock()
        session.add = MagicMock()
        yield session

    app.dependency_overrides[get_service_auth_b64] = mock_get_service_auth_b64
    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.clear()


def card():
    return {
        "task": {"Id": 701, "Name": "Выдать Wi-Fi", "Description": "Нужен доступ", "StatusId": 26, "CreatorLogin": "operator.test"},
        "history": [{"Id": 1, "Comment": "Создана"}],
        "suggested_action": {"rule_type": "wlan_access", "status_id": 29},
        "ai_suggested_resolution": "Доступ будет предоставлен после подтверждения.",
    }


@pytest.mark.asyncio
async def test_suggestion_manual_change_stale_then_recalculate_e2e():
    """AI proposal -> manual update -> stale -> explicit recalculation."""
    redis = MemoryRedis()
    with patch("app.routers.triage.get_redis_client", return_value=redis), patch(
        "app.routers.triage.TriageService.get_task_card_details", new_callable=AsyncMock, side_effect=[card(), card(), card()]
    ), patch(
        "app.routers.triage.TriageService.apply_triage_resolution", new_callable=AsyncMock,
        return_value=[{"task_id": 701, "status": "success", "update_ok": True, "expenses_ok": True}],
    ), patch("app.routers.triage.enforce_triage_apply_rate_limit", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            proposal = await client.get("/api/v1/triage/tasks/701", headers=HEADERS)
            assert proposal.status_code == 200
            first = proposal.json()["ai_suggestion"]
            assert first["state"] == "current"
            assert first["source"] == "Rule Engine + AI Hub/RAG"
            assert first["policy"]["mode"] == "confirm"

            manual = await client.post(
                "/api/v1/triage/apply", headers=HEADERS,
                json={"task_ids": [701], "status_id": 27, "comment": "Взято оператором", "expenses": 0},
            )
            assert manual.status_code == 200

            stale = await client.get("/api/v1/triage/tasks/701", headers=HEADERS)
            assert stale.status_code == 200
            assert stale.json()["ai_suggestion"]["state"] == "stale"
            assert "вручную" in stale.json()["ai_suggestion"]["stale_reason"].lower()

            recalculated = await client.post("/api/v1/triage/tasks/701/reanalyze", headers=HEADERS)
            assert recalculated.status_code == 200
            final = recalculated.json()["ai_suggestion"]
            assert final["state"] == "current"
            assert final["fingerprint"] == first["fingerprint"]
            assert final["calculated_at"] >= first["calculated_at"]


@pytest.mark.asyncio
async def test_unsafe_auto_is_demoted_and_nothing_executes_before_confirm_e2e():
    redis = MemoryRedis()
    # This is the current proposal that the web UI binds to the command.
    redis.values["ai:suggestion:702"] = json.dumps({
        "task_id": 702, "state": "current", "fingerprint": "current-version",
        "policy": {"blocked": False}, "missing_data": [],
    })
    with patch("app.routers.commands.get_redis_client", return_value=redis):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            queued = await client.post(
                "/api/v1/commands", headers=HEADERS,
                json={
                    "type": "grant_wlan", "target": {"task_id": 702}, "params": {"username": "operator.test"},
                    "mode": "auto", "source": "web", "suggestion_task_id": 702,
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
                f"/api/v1/commands/{job_id}/confirm", headers=HEADERS,
                json={"decision": "approve"},
            )
            redis.lpush.assert_awaited_once()
