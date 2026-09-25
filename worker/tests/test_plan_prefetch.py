"""Tests for background plan prefetch task and Redis cache delivery."""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.autopilot.dto import AgentPlanDTO
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from worker.src.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult
from worker.src.scenarios.registry import ScenarioRegistry
from worker.src.services.auth import ServiceAuthCredentials
from worker.src.tasks.plan_prefetch import (
    prefetch_agent_plan_task,
    set_prefetch_client,
    set_prefetch_redis_client,
    set_prefetch_registry,
    set_prefetch_service_auth,
)
from worker.tests.test_autopilot_task import MockRedis


class DummyPrinterScenario(BaseScenario):
    scenario_key = "install_printer"
    name = "Установка принтера"
    description = "Настройка сетевого принтера"

    async def can_handle(self, task: TaskDTO) -> bool:
        return True

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: Any = None) -> ScenarioExecutionResult:
        return ScenarioExecutionResult(
            success=True,
            action_taken="install_printer",
            resolution_comment="Принтер настроен.",
            technical_note="OK",
        )


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

    registry = ScenarioRegistry()
    registry.register(DummyPrinterScenario())

    set_prefetch_client(mock_client)
    set_prefetch_service_auth(mock_auth)
    set_prefetch_redis_client(mock_redis)
    set_prefetch_registry(registry)

    yield mock_client, mock_auth, mock_redis, registry

    set_prefetch_client(None)
    set_prefetch_service_auth(None)
    set_prefetch_redis_client(None)
    set_prefetch_registry(None)


@pytest.mark.asyncio
async def test_prefetch_agent_plan_caches_in_redis(mock_prefetch_env):
    mock_client, _, mock_redis, _ = mock_prefetch_env

    mock_client.get_task.return_value = TaskDTO(
        id=8888,
        name="Установить принтер",
        description="Подключите принтер на ПК WKS-404",
        status_id=1,
        status_name="Новая",
        entities=ExtractedEntitiesDTO(pc_name="WKS-404", printer_model="HP LaserJet"),
    )
    mock_client.get_task_lifetime.return_value = []

    res = await prefetch_agent_plan_task(8888)
    assert res["status"] == "prefetched"
    assert res["ticket_id"] == 8888
    assert res["scenario"] == "install_printer"

    # Verify Redis cache has the serialized AgentPlanDTO
    cache_key = "cache:autopilot:plan:8888"
    cached_val = await mock_redis.get(cache_key)
    assert cached_val is not None

    plan_dto = AgentPlanDTO.model_validate_json(cached_val)
    assert plan_dto.task_id == 8888
    assert plan_dto.scenario_key == "install_printer"
    assert plan_dto.target_status_id == 3
    assert "WKS-404" in plan_dto.suggested_comment
