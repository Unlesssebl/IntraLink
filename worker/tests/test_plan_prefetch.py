import uuid
from unittest.mock import AsyncMock

import pytest

from core.autopilot.dto import AgentPlanDTO, AutopilotPolicyDTO
from core.intraservice.auth import ServiceAuthCredentials
from worker.src.tasks.plan_prefetch import (
    prefetch_agent_plan_task,
    set_prefetch_analysis_service,
    set_prefetch_client,
    set_prefetch_policy_service,
    set_prefetch_redis_client,
    set_prefetch_service_auth,
)
from worker.tests.test_autopilot_task import MockRedis


@pytest.fixture
def mock_prefetch_env():
    mock_client = AsyncMock()
    mock_auth = AsyncMock()
    mock_auth.bootstrap_auth.return_value = ServiceAuthCredentials(
        auth_b64="bW9jazp0b2tlbg==",
        bot_user_id=999,
        login="alen_assistant",
    )
    mock_redis = MockRedis()

    mock_policy_service = AsyncMock()
    mock_policy_service.get_policy.return_value = AutopilotPolicyDTO(
        scenario_key="install_printer",
        mode="ASSISTED",
        min_confidence=0.85,
    )

    mock_analysis_service = AsyncMock()

    set_prefetch_client(mock_client)
    set_prefetch_service_auth(mock_auth)
    set_prefetch_redis_client(mock_redis)
    set_prefetch_policy_service(mock_policy_service)
    set_prefetch_analysis_service(mock_analysis_service)

    yield mock_client, mock_auth, mock_redis, mock_analysis_service

    set_prefetch_client(None)
    set_prefetch_service_auth(None)
    set_prefetch_redis_client(None)
    set_prefetch_policy_service(None)
    set_prefetch_analysis_service(None)


@pytest.mark.asyncio
async def test_prefetch_agent_plan_calls_analysis_service(mock_prefetch_env):
    mock_client, _, mock_redis, mock_analysis_service = mock_prefetch_env

    plan_id = uuid.uuid4()
    mock_plan = AgentPlanDTO(
        task_id=8888,
        scenario_key="install_printer",
        scenario_name="Установка принтера",
        matched=True,
        target_status_id=3,
        suggested_comment="Принтер настроен на WKS-404",
        technical_note="OK",
        policy_mode="ASSISTED",
        is_executable=True,
        plan_id=plan_id,
        plan_hash="hash_8888_test",
        routing_state="selected",
        decision_reason_codes=["exact_catalog_match"],
        analysis_state="ready",
    )
    mock_analysis_service.analyze_ticket.return_value = mock_plan

    res = await prefetch_agent_plan_task(8888)
    assert res["status"] == "prefetched"
    assert res["ticket_id"] == 8888
    assert res["scenario"] == "install_printer"
    assert res["routing_state"] == "selected"
    assert res["plan_hash"] == "hash_8888_test"

    mock_analysis_service.analyze_ticket.assert_awaited_once_with(
        ticket_id=8888,
        force=False,
        auth_b64="bW9jazp0b2tlbg==",
        redis_client=mock_redis,
    )
