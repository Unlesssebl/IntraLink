"""Resilience tests for semantic routing: handling typos, slang and missing keywords."""

from unittest.mock import AsyncMock

import pytest

from core.intraservice.dto import TaskDTO
from worker.src.scenarios.grant_wlan import GrantWLANScenario
from worker.src.scenarios.install_printer import InstallPrinterScenario
from worker.src.scenarios.rag_consultation import RAGConsultationScenario
from worker.src.scenarios.router import ScenarioRouter


@pytest.mark.asyncio
async def test_typo_resilience_grant_wlan():
    """Verify that colloquial ticket ('хочу к вайфай') matches via semantic prototype."""
    wlan_scen = GrantWLANScenario()
    printer_scen = InstallPrinterScenario()
    rag_scen = RAGConsultationScenario()

    scenarios = {
        "grant_wlan": wlan_scen,
        "install_printer": printer_scen,
        "rag_consultation": rag_scen,
    }

    # Ticket has colloquial 'вайфай' and does not match exact keywords
    task = TaskDTO(
        Id=9901,
        Name="Хочу к вайфай",
        Description="добавьте меня к беспроводной, не могу подключиться к корп сети вайфай",
    )

    # Mock semantic index scores
    mock_ai = AsyncMock()
    router = ScenarioRouter(ai_client=mock_ai)
    router._semantic_index._is_ready = True
    router._semantic_index._prototype_vectors = {"grant_wlan": [[1.0] * 1024]}

    async def mock_score(text: str):
        return {
            "grant_wlan": 0.88,  # High semantic affinity
            "install_printer": 0.12,
            "rag_consultation": 0.40,
        }

    router._semantic_index.score = mock_score

    route_res = await router.route_task(task, scenarios, threshold=0.55)
    assert route_res is not None
    matched_scenario, match_dto = route_res
    assert matched_scenario.scenario_key == "grant_wlan"
    assert match_dto.matched is True
    assert match_dto.confidence >= 0.55
    assert any("Factor A" in r or "Factor E" in r for r in match_dto.reasons)


@pytest.mark.asyncio
async def test_slang_resilience_install_printer():
    """Verify ticket with non-standard phrasing matches printer scenario."""
    printer_scen = InstallPrinterScenario()
    wlan_scen = GrantWLANScenario()
    rag_scen = RAGConsultationScenario()

    scenarios = {
        "install_printer": printer_scen,
        "grant_wlan": wlan_scen,
        "rag_consultation": rag_scen,
    }

    task = TaskDTO(
        Id=9902,
        Name="МФУ в 305 кабинете",
        Description="надо привязать сетевой аппарат для сканов к компу",
    )

    mock_ai = AsyncMock()
    router = ScenarioRouter(ai_client=mock_ai)
    router._semantic_index._is_ready = True

    async def mock_score(text: str):
        return {
            "install_printer": 0.85,
            "grant_wlan": 0.10,
            "rag_consultation": 0.35,
        }

    router._semantic_index.score = mock_score

    route_res = await router.route_task(task, scenarios, threshold=0.55)
    assert route_res is not None
    matched_scenario, match_dto = route_res
    assert matched_scenario.scenario_key == "install_printer"
    assert match_dto.matched is True
