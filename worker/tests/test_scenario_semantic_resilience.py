"""Resilience tests for semantic routing: handling typos, slang and missing keywords."""

from unittest.mock import AsyncMock

import pytest

from core.intraservice.dto import TaskDTO
from worker.src.scenarios.ad_password_reset import ADPasswordResetScenario
from worker.src.scenarios.install_printer import InstallPrinterScenario
from worker.src.scenarios.rag_consultation import RAGConsultationScenario
from worker.src.scenarios.router import ScenarioRouter


@pytest.mark.asyncio
async def test_typo_resilience_ad_password_reset():
    """Verify that typo-ridden ticket ('забыла парольчик') matches via semantic prototype."""
    ad_scen = ADPasswordResetScenario()
    printer_scen = InstallPrinterScenario()
    rag_scen = RAGConsultationScenario()

    scenarios = {
        "ad_password_reset": ad_scen,
        "install_printer": printer_scen,
        "rag_consultation": rag_scen,
    }

    # Ticket has colloquial 'парольчик' and does not match exact keywords
    task = TaskDTO(
        Id=9901,
        Name="Не пускает в винду",
        Description="забыла парольчик от учетки, пустите плиз в комп срочно",
    )

    # Mock semantic index scores
    mock_ai = AsyncMock()
    router = ScenarioRouter(ai_client=mock_ai)
    router._semantic_index._is_ready = True
    router._semantic_index._prototype_vectors = {"ad_password_reset": [[1.0] * 1024]}

    async def mock_score(text: str):
        return {
            "ad_password_reset": 0.88,  # High semantic affinity
            "install_printer": 0.12,
            "rag_consultation": 0.40,
        }

    router._semantic_index.score = mock_score

    route_res = await router.route_task(task, scenarios, threshold=0.55)
    assert route_res is not None
    matched_scenario, match_dto = route_res
    assert matched_scenario.scenario_key == "ad_password_reset"
    assert match_dto.matched is True
    assert match_dto.confidence >= 0.55
    assert any("Factor A" in r for r in match_dto.reasons)
    assert any("Factor E" in r for r in match_dto.reasons)


@pytest.mark.asyncio
async def test_slang_resilience_install_printer():
    """Verify ticket with non-standard phrasing matches printer scenario."""
    printer_scen = InstallPrinterScenario()
    ad_scen = ADPasswordResetScenario()
    rag_scen = RAGConsultationScenario()

    scenarios = {
        "install_printer": printer_scen,
        "ad_password_reset": ad_scen,
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
            "ad_password_reset": 0.10,
            "rag_consultation": 0.35,
        }

    router._semantic_index.score = mock_score

    route_res = await router.route_task(task, scenarios, threshold=0.55)
    assert route_res is not None
    matched_scenario, match_dto = route_res
    assert matched_scenario.scenario_key == "install_printer"
    assert match_dto.matched is True
