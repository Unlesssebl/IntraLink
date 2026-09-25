"""Tests for Multi-Factor Scenario Router and Catalog-First Prior clustering."""

import pytest

from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.scenarios.adapters.account_create import AccountCreateScenario
from core.scenarios.adapters.account_lock import AccountLockScenario
from core.scenarios.adapters.default_printer_fix import DefaultPrinterFixScenario
from core.scenarios.adapters.grant_wlan import GrantWLANScenario
from core.scenarios.adapters.install_printer import InstallPrinterScenario
from core.scenarios.adapters.printer_spooler_restart import PrinterSpoolerRestartScenario
from core.scenarios.catalog_prior import CatalogPriorProvider
from core.scenarios.router import ScenarioRouter


@pytest.fixture
def printer_scenarios():
    return {
        "install_printer": InstallPrinterScenario(),
        "printer_spooler_restart": PrinterSpoolerRestartScenario(),
        "default_printer_fix": DefaultPrinterFixScenario(),
    }


@pytest.fixture
def all_scenarios(printer_scenarios):
    return {
        **printer_scenarios,
        "account_create": AccountCreateScenario(),
        "account_lock": AccountLockScenario(),
        "grant_wlan": GrantWLANScenario(),
    }


def test_catalog_prior_provider_cluster_multi_scenario():
    """Verify service 19 gives catalog priors to all scenarios in the printing domain cluster."""
    provider = CatalogPriorProvider()
    task = TaskDTO(
        Id=101,
        ServiceId=19,
        ServiceName="Обслуживание оргтехники и заправка картриджей",
    )
    priors = provider.get_priors(task)
    assert "install_printer" in priors
    assert "printer_spooler_restart" in priors
    assert "default_printer_fix" in priors
    assert priors["install_printer"][0] >= 0.90
    assert priors["printer_spooler_restart"][0] >= 0.90
    assert priors["default_printer_fix"][0] >= 0.90


@pytest.mark.asyncio
async def test_router_service_19_spooler_restart_wins_over_install(printer_scenarios):
    """DoD Criterion 2.1: Ticket in Service 19 for spooler crash routes to printer_spooler_restart."""
    router = ScenarioRouter()
    task = TaskDTO(
        Id=201,
        ServiceId=19,
        ServiceName="Обслуживание оргтехники и заправка картриджей",
        Name="Зависла печать",
        Description="В очереди висят документы на WKS-042, принтер не реагирует",
        Entities=ExtractedEntitiesDTO(pc_name="WKS-042"),
    )

    routed = await router.route_task(task, printer_scenarios)
    assert routed is not None
    winning_scenario, match = routed

    assert winning_scenario.scenario_key == "printer_spooler_restart"
    assert match.confidence >= 0.50
    assert any("Factor A" in r for r in match.reasons)
    assert any("Factor B (Catalog Prior)" in r for r in match.reasons)


@pytest.mark.asyncio
async def test_router_service_19_install_printer_wins_over_spooler(printer_scenarios):
    """DoD Criterion 2.2: Ticket in Service 19 for new printer setup routes to install_printer."""
    router = ScenarioRouter()
    task = TaskDTO(
        Id=202,
        ServiceId=19,
        ServiceName="Обслуживание оргтехники и заправка картриджей",
        Name="Установка принтера",
        Description="Прошу подключить сетевой принтер 10.244.1.20 к WKS-0050",
        Entities=ExtractedEntitiesDTO(pc_name="WKS-0050", printer_address="10.244.1.20"),
    )

    routed = await router.route_task(task, printer_scenarios)
    assert routed is not None
    winning_scenario, match = routed

    assert winning_scenario.scenario_key == "install_printer"
    assert match.confidence >= 0.50
    assert any("Factor A" in r for r in match.reasons)
    assert any("Factor B (Catalog Prior)" in r for r in match.reasons)


@pytest.mark.asyncio
async def test_router_service_19_default_printer_wins(printer_scenarios):
    """DoD Criterion 2.3: Ticket in Service 19 for setting default printer routes to default_printer_fix."""
    router = ScenarioRouter()
    task = TaskDTO(
        Id=203,
        ServiceId=19,
        ServiceName="Обслуживание оргтехники и заправка картриджей",
        Name="Слетел принтер",
        Description="Поставить по умолчанию принтер Canon MF440 на компьютере WKS-007",
        Entities=ExtractedEntitiesDTO(pc_name="WKS-007", printer_model="Canon MF440"),
    )

    routed = await router.route_task(task, printer_scenarios)
    assert routed is not None
    winning_scenario, match = routed

    assert winning_scenario.scenario_key == "default_printer_fix"
    assert match.confidence >= 0.50


@pytest.mark.asyncio
async def test_router_cross_domain_prior_routing(all_scenarios):
    """Verify distinct domain service IDs route cleanly to their target scenarios."""
    router = ScenarioRouter()

    # Service 55 -> account_create
    task_user = TaskDTO(
        Id=301,
        ServiceId=55,
        TaskTypeId=1018,
        Name="Заявка на пользователя Directum",
        Description="Создать учетную запись для нового сотрудника Иванова Ивана",
        Entities=ExtractedEntitiesDTO(last_name="Иванов", first_name="Иван"),
    )
    routed_user = await router.route_task(task_user, all_scenarios)
    assert routed_user is not None
    assert routed_user[0].scenario_key == "account_create"

    # Service 8 -> account_lock
    task_lock = TaskDTO(
        Id=302,
        ServiceId=8,
        Name="Увольнение сотрудника",
        Description="Заблокировать учетную запись petrov.p в связи с увольнением",
        Entities=ExtractedEntitiesDTO(target_user="petrov.p"),
    )
    routed_lock = await router.route_task(task_lock, all_scenarios)
    assert routed_lock is not None
    assert routed_lock[0].scenario_key == "account_lock"

    # Service 63 -> grant_wlan
    task_wifi = TaskDTO(
        Id=303,
        ServiceId=63,
        Name="Доступ к Wi-Fi",
        Description="Прошу предоставить доступ к корпоративной сети Wi-Fi",
        ApplicantName="Сидоров Сидор",
        Entities=ExtractedEntitiesDTO(target_user="sidorov.s"),
    )
    routed_wifi = await router.route_task(task_wifi, all_scenarios)
    assert routed_wifi is not None
    assert routed_wifi[0].scenario_key == "grant_wlan"
