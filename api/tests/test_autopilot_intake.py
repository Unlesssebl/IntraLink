from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from api.src.core.config import settings
from api.src.core.db import get_db_session
from api.src.core.redis import get_redis
from api.src.features.autopilot.router import get_automation_service
from api.src.features.autopilot.schemas import TicketAutomationDTO
from api.src.main import app
from core.automation.contracts import (
    CaseCandidate,
    CaseDecision,
    CaseDecisionState,
    CaseFrame,
    WorkflowPlan,
    WorkflowPlanState,
)
from core.automation.service_routing import ServiceCompatibilityDecision, ServiceCompatibilityState


def _dummy_automation_dto(ticket_id: int = 101) -> TicketAutomationDTO:
    fid = uuid4()
    did = uuid4()
    cid = uuid4()
    wid = uuid4()
    snapshot_hash = "a" * 64

    return TicketAutomationDTO(
        task_id=ticket_id,
        snapshot_hash=snapshot_hash,
        case_frame=CaseFrame(
            id=fid,
            task_id=ticket_id,
            snapshot_hash=snapshot_hash,
            frame_version="1.0.0",
        ),
        case_decision=CaseDecision(
            id=did,
            task_id=ticket_id,
            frame_id=fid,
            snapshot_hash=snapshot_hash,
            router_version="1.0.0",
            state=CaseDecisionState.selected,
            primary_case_type="guest_wifi",
            candidates=[CaseCandidate(case_type="guest_wifi", case_type_version="1.0.0")],
        ),
        service_compatibility=ServiceCompatibilityDecision(
            id=cid,
            task_id=ticket_id,
            case_decision_id=did,
            snapshot_hash=snapshot_hash,
            state=ServiceCompatibilityState.compatible,
        ),
        workflow_plan=WorkflowPlan(
            id=wid,
            case_decision_id=did,
            task_id=ticket_id,
            snapshot_hash=snapshot_hash,
            workflow_key="guest_wifi_workflow",
            workflow_version="1.0.0",
            state=WorkflowPlanState.ready_for_authorization,
            disposition="execute",
        ),
    )


@pytest.mark.asyncio
async def test_intake_endpoint_unauthorized():
    transport = ASGITransport(app=app)
    with patch.object(settings, "WORKER_API_KEY", "correct-worker-key"):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. No header
            resp = await client.post("/api/v2/internal/autopilot/tickets/101/intake")
            assert resp.status_code == 401

            # 2. Invalid header
            resp = await client.post(
                "/api/v2/internal/autopilot/tickets/101/intake",
                headers={"X-Worker-Key": "wrong-key"},
            )
            assert resp.status_code == 401


@pytest.mark.asyncio
async def test_intake_endpoint_lock_conflict():
    mock_db = AsyncMock()
    mock_service = AsyncMock()
    mock_redis = AsyncMock()
    # Mock DistributedTaskLock to return False on acquire (simulating conflict)
    mock_redis.set.return_value = False

    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_automation_service] = lambda: mock_service
    app.dependency_overrides[get_redis] = lambda: mock_redis

    transport = ASGITransport(app=app)
    try:
        with patch.object(settings, "WORKER_API_KEY", "valid-key"):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v2/internal/autopilot/tickets/101/intake",
                    headers={"X-Worker-Key": "valid-key"},
                )
                assert resp.status_code == 409
                assert resp.json()["detail"] == "analysis_already_running"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_intake_endpoint_success():
    mock_db = AsyncMock()
    mock_service = AsyncMock()
    mock_service.analyze.return_value = _dummy_automation_dto(ticket_id=101)

    mock_redis = AsyncMock()
    mock_redis.set.return_value = True  # lock acquired successfully
    mock_redis.eval.return_value = 1   # lock released successfully

    app.dependency_overrides[get_db_session] = lambda: mock_db
    app.dependency_overrides[get_automation_service] = lambda: mock_service
    app.dependency_overrides[get_redis] = lambda: mock_redis

    transport = ASGITransport(app=app)
    try:
        with patch.object(settings, "WORKER_API_KEY", "valid-key"):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v2/internal/autopilot/tickets/101/intake",
                    headers={"X-Worker-Key": "valid-key"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["task_id"] == 101
                assert data["workflow_plan"]["workflow_key"] == "guest_wifi_workflow"

                # Verify service.analyze was called with auth_b64=None and force=False
                mock_service.analyze.assert_awaited_once_with(
                    mock_db,
                    ticket_id=101,
                    auth_b64=None,
                    force=False,
                )
    finally:
        app.dependency_overrides.clear()
