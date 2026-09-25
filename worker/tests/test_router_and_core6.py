"""Tests for Multi-Factor Scenario Router, Coherence Guard, and Core-6 scenarios."""

import pytest

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from worker.src.scenarios.catalog_prior import CatalogPriorProvider
from worker.src.scenarios.coherence_guard import CoherenceGuard, CoherenceStatus
from worker.src.scenarios.grant_wlan import GrantWLANScenario
from worker.src.scenarios.offline_host import OfflineHostScenario
from worker.src.scenarios.registry import get_default_scenario_registry
from worker.src.scenarios.router import ScenarioRouter
from worker.src.scenarios.service_redirect import ServiceRedirectScenario


# -------------------------------------------------------------------
# 1. CatalogPriorProvider Tests
# -------------------------------------------------------------------
def test_catalog_prior_exact_service_id():
    provider = CatalogPriorProvider()

    # Wi-Fi Service
    task_wifi = TaskDTO(Id=1, Name="Заявка", ServiceId=63)
    res_wifi = provider.get_prior(task_wifi)
    assert res_wifi is not None
    assert res_wifi[0] == "grant_wlan"
    assert res_wifi[1] >= 0.90

    # Printer Service
    task_printer = TaskDTO(Id=2, Name="Заявка", ServiceId=62)
    res_prn = provider.get_prior(task_printer)
    assert res_prn is not None
    assert res_prn[0] == "install_printer"

    # Offline/Hardware Service
    task_offline = TaskDTO(Id=3, Name="Заявка", ServiceId=112)
    res_off = provider.get_prior(task_offline)
    assert res_off is not None
    assert res_off[0] == "offline_host"


def test_catalog_prior_service_name_fuzzy():
    provider = CatalogPriorProvider()
    task = TaskDTO(Id=4, Name="Заявка", ServiceName="Установка сетевых принтеров и сканеров")
    res = provider.get_prior(task)
    assert res is not None
    assert res[0] == "install_printer"


# -------------------------------------------------------------------
# 2. Coherence Guard Tests
# -------------------------------------------------------------------
def test_coherence_guard_coherent():
    guard = CoherenceGuard()
    task = TaskDTO(Id=10, Name="Подключение принтера", Description="Пожалуйста установите принтер на WKS-1")
    res = guard.evaluate("install_printer", task)
    assert res.status == CoherenceStatus.COHERENT
    assert res.confidence_delta > 0


def test_coherence_guard_divergent_collision():
    guard = CoherenceGuard()
    # Ticket is about Wi-Fi access, but candidate is install_printer → clear divergence
    task = TaskDTO(
        Id=11,
        Name="Предоставьте доступ к беспроводной сети",
        Description="Нет доступа к wi-fi в офисе, прошу добавить в группу WLAN-WORKNET",
    )
    res = guard.evaluate("install_printer", task)
    assert res.status == CoherenceStatus.DIVERGENT
    assert res.confidence_delta < 0
    assert res.divergent_scenario == "grant_wlan"


# -------------------------------------------------------------------
# 3. ScenarioRouter Multi-Factor Scoring
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_router_selects_grant_wlan_on_service_and_text():
    registry = get_default_scenario_registry()
    router = ScenarioRouter()

    task = TaskDTO(
        Id=20,
        Name="Подключить ноутбук к корпоративному Wi-Fi",
        Description="Выдайте доступ к беспроводной сети WLAN-WORKNET",
        ServiceId=63,
        ApplicantName="Иванов И.И.",
    )

    routed = await router.route_task(task, registry._scenarios)
    assert routed is not None
    scenario, match = routed
    assert scenario.scenario_key == "grant_wlan"
    assert match.confidence >= 0.80
    assert match.matched is True


@pytest.mark.asyncio
async def test_router_resolves_divergence_towards_real_intent():
    registry = get_default_scenario_registry()
    router = ScenarioRouter()

    # User mistakenly selected Printer service (62), but text is about Wi-Fi
    task = TaskDTO(
        Id=21,
        Name="Доступ к Wi-Fi",
        Description="Подключите пожалуйста мой телефон к сети WLAN-WORKNET",
        ServiceId=62,  # Misclassified catalog!
        ApplicantName="Петров П.П.",
    )

    routed = await router.route_task(task, registry._scenarios)
    assert routed is not None
    scenario, match = routed
    # Router must NOT choose printer because of Divergence penalty, must choose Wi-Fi
    assert scenario.scenario_key == "grant_wlan"


# -------------------------------------------------------------------
# 4. Core-6: GrantWLANScenario
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grant_wlan_scenario_execution():
    scenario = GrantWLANScenario()
    task = TaskDTO(
        Id=30,
        Name="Wi-Fi",
        Description="Подключить к WLAN-WORKNET",
        ApplicantName="Сидоров С.С.",
    )
    policy = AutopilotPolicyDTO(scenario_key="grant_wlan", mode="FULL_AUTO", min_confidence=0.85)

    precond = await scenario.validate_preconditions(task)
    assert precond.is_valid is True

    res = await scenario.execute(task, policy)
    assert res.success is True
    assert res.action_taken == "grant_wlan_access"
    assert res.target_status_id == 3
    assert "WLAN-WORKNET" in res.resolution_comment


# -------------------------------------------------------------------
# 5. Core-6: ServiceRedirectScenario (Relevance Gateway / Filter #1)
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_service_redirect_cancellation():
    scenario = ServiceRedirectScenario()
    task = TaskDTO(
        Id=40,
        Name="Сломался стул и перегорела лампочка",
        Description="Почините пожалуйста ножку стула в кабинете 204",
    )
    policy = AutopilotPolicyDTO(scenario_key="service_redirect", mode="FULL_AUTO", min_confidence=0.85)

    assert await scenario.can_handle(task) is True
    res = await scenario.execute(task, policy)
    assert res.success is True
    assert res.target_status_id == 30  # Cancelled
    assert "АХО" in res.resolution_comment


# -------------------------------------------------------------------
# 6. Core-6: OfflineHostScenario
# -------------------------------------------------------------------
@pytest.mark.asyncio
async def test_offline_host_missing_facts_suspends():
    scenario = OfflineHostScenario()
    task = TaskDTO(
        Id=50,
        Name="Не включается компьютер",
        Description="Черный экран, помогите!",
        entities=ExtractedEntitiesDTO(),  # No PC name
    )

    precond = await scenario.validate_preconditions(task)
    assert precond.is_valid is False
    assert "pc_name" in precond.missing_facts
    assert precond.clarification_prompt is not None


@pytest.mark.asyncio
async def test_offline_host_execution_dispatches_field_engineer(monkeypatch):
    scenario = OfflineHostScenario()
    task = TaskDTO(
        Id=51,
        Name="Не включается пк",
        Description="Компьютер не реагирует на кнопку",
        entities=ExtractedEntitiesDTO(pc_name="WKS-DEAD-HOST"),
    )
    policy = AutopilotPolicyDTO(scenario_key="offline_host", mode="FULL_AUTO", min_confidence=0.85)

    precond = await scenario.validate_preconditions(task)
    assert precond.is_valid is True

    # Simulate host ports unreachable
    async def mock_unreachable(host, port, timeout=1.0):
        return False

    monkeypatch.setattr("core.scenarios.adapters.offline_host.check_tcp_port", mock_unreachable)

    res = await scenario.execute(task, policy)
    assert res.success is True
    assert res.target_status_id == 2  # In progress with field team
    assert "112" in res.resolution_comment
    assert "dispatch_field_engineer" == res.action_taken
