"""Concurrency isolation tests for RAGConsultationScenario."""

from unittest.mock import patch

import pytest

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from core.rag.search import KnowledgeSolutionDTO
from worker.src.scenarios.rag_consultation import RAGConsultationScenario


@pytest.fixture
def default_policy() -> AutopilotPolicyDTO:
    return AutopilotPolicyDTO(
        scenario_key="rag_consultation",
        mode="FULL_AUTO",
        min_confidence=0.80,
    )


@pytest.mark.asyncio
async def test_concurrent_tasks_solution_isolation(default_policy):
    """Verify that multiple concurrent tasks do not cross-contaminate cached solutions."""
    scenario = RAGConsultationScenario()

    task_a = TaskDTO(Id=101, Name="Настройка почты", Description="Как настроить почту на iOS")
    task_b = TaskDTO(Id=102, Name="Настройка VPN", Description="Как настроить Wireguard VPN")

    solution_a = KnowledgeSolutionDTO(
        task_id=1001,
        original_name="Инструкция Mail",
        problem="Настройка почты",
        solution="Инструкция для Mail: сервер mail.loc",
        service_id=1,
        service_name="Почта",
        status_name="Выполнена",
        quality_score=1.0,
        similarity=0.95,
    )

    solution_b = KnowledgeSolutionDTO(
        task_id=1002,
        original_name="Инструкция VPN",
        problem="Настройка VPN",
        solution="Инструкция для VPN: скачать конфиг с vpn.loc",
        service_id=2,
        service_name="Сеть",
        status_name="Выполнена",
        quality_score=1.0,
        similarity=0.91,
    )

    def mock_search(*args, **kwargs):
        query_vector = kwargs.get("query_vector") or (args[2] if len(args) > 2 else None)
        # Return different solution depending on vector marker or inspection
        if query_vector and query_vector[0] == 0.1:
            return [solution_a]
        return [solution_b]

    async def mock_embed(text, ai_client, **kwargs):
        if "почт" in text.lower():
            return [0.1] * 1024
        return [0.2] * 1024

    with (
        patch("core.scenarios.adapters.rag_consultation.get_embedding_vector", side_effect=mock_embed),
        patch("core.scenarios.adapters.rag_consultation.search_hybrid_solutions", side_effect=mock_search),
    ):

        # 1. Validate both tasks (preconditions)
        precond_a = await scenario.validate_preconditions(task_a)
        assert precond_a.is_valid is True

        precond_b = await scenario.validate_preconditions(task_b)
        assert precond_b.is_valid is True

        # In old code: scenario._cached_solution would now be solution_b!
        # Both task_a and task_b solutions are safely isolated in dict.

        # 2. Execute task_a: MUST receive solution_a, NOT solution_b
        res_a = await scenario.execute(task_a, default_policy)
        assert res_a.success is True
        assert "Инструкция для Mail" in res_a.resolution_comment
        assert "Тикет #1001" in res_a.technical_note

        # 3. Execute task_b: MUST receive solution_b
        res_b = await scenario.execute(task_b, default_policy)
        assert res_b.success is True
        assert "Инструкция для VPN" in res_b.resolution_comment
        assert "Тикет #1002" in res_b.technical_note

        # 4. Check registry is cleaned up
        assert 101 not in scenario._solutions_by_task
        assert 102 not in scenario._solutions_by_task
