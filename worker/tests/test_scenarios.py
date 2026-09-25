"""Unit and integration tests for Core-3 autopilot scenarios."""

from unittest.mock import patch

import pytest

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.rag.search import KnowledgeSolutionDTO
from worker.src.scenarios.grant_wlan import GrantWLANScenario
from worker.src.scenarios.install_printer import InstallPrinterScenario
from worker.src.scenarios.offline_host import OfflineHostScenario
from worker.src.scenarios.rag_consultation import RAGConsultationScenario
from worker.src.scenarios.registry import ScenarioRegistry


@pytest.fixture
def default_policy() -> AutopilotPolicyDTO:
    return AutopilotPolicyDTO(
        scenario_key="test_scenario",
        mode="FULL_AUTO",
        min_confidence=0.85,
    )


# -------------------------------------------------------------
# 1. InstallPrinterScenario Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_install_printer_can_handle():
    scenario = InstallPrinterScenario()

    # Match valid printer install request
    task1 = TaskDTO(Id=101, Name="Установка принтера Kyocera", Description="Подключите принтер на ПК")
    assert await scenario.can_handle(task1) is True

    # Reject audio peripherals
    task_audio = TaskDTO(Id=102, Name="Настройка наушников", Description="Колонки и микрофон не работают")
    assert await scenario.can_handle(task_audio) is False


@pytest.mark.asyncio
async def test_install_printer_preconditions_missing_facts():
    scenario = InstallPrinterScenario()

    # Missing PC name
    task_no_pc = TaskDTO(
        Id=103,
        Name="Подключить принтер",
        entities=ExtractedEntitiesDTO(printer_address="192.168.1.50"),
    )
    res_no_pc = await scenario.validate_preconditions(task_no_pc)
    assert res_no_pc.is_valid is False
    assert "pc_name" in res_no_pc.missing_facts
    assert "укажите сетевое имя" in res_no_pc.clarification_prompt

    # Missing Printer address/model (host IS online, so ping must succeed)
    task_no_printer = TaskDTO(
        Id=104,
        Name="Подключить принтер",
        entities=ExtractedEntitiesDTO(pc_name="WKS-1020"),
    )
    with (
        patch("core.scenarios.adapters.install_printer.fast_ping", return_value={"host": "WKS-1020", "is_online": True}),
        patch("core.scenarios.adapters.install_printer.probe_diagnostic_ports", return_value=[{"port": 5985, "is_open": True}, {"port": 445, "is_open": True}]),
    ):
        res_no_printer = await scenario.validate_preconditions(task_no_printer)
    assert res_no_printer.is_valid is False
    assert "printer_address" in res_no_printer.missing_facts


@pytest.mark.asyncio
async def test_install_printer_preconditions_host_offline():
    scenario = InstallPrinterScenario()
    task = TaskDTO(
        Id=105,
        Name="Подключить принтер",
        entities=ExtractedEntitiesDTO(pc_name="WKS-OFFLINE", printer_address="10.0.0.1"),
    )

    with (
        patch("core.scenarios.adapters.install_printer.probe_diagnostic_ports", return_value={"smb_445": False, "winrm_5985": False}),
        patch("core.scenarios.adapters.install_printer.fast_ping", return_value={"host": "WKS-OFFLINE", "is_online": False}),
    ):
        res = await scenario.validate_preconditions(task)
        assert res.is_valid is False
        assert "host_offline" in res.environment_barriers
        assert "включите ПК" in res.clarification_prompt


@pytest.mark.asyncio
async def test_install_printer_executor_unavailable(default_policy):
    scenario = InstallPrinterScenario()
    task = TaskDTO(
        Id=106,
        Name="Установка принтера",
        entities=ExtractedEntitiesDTO(pc_name="WKS-ONLINE", printer_address="192.168.1.100"),
    )

    exec_res = await scenario.execute(task, default_policy)

    assert exec_res.success is False
    assert exec_res.target_status_id == 2
    assert exec_res.error == "printer_executor_unavailable"
    assert exec_res.metadata["safe_to_retry"] is False


# -------------------------------------------------------------
# 2. GrantWLANScenario Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_grant_wlan_can_handle():
    scenario = GrantWLANScenario()

    task_match = TaskDTO(Id=201, Name="Подключение к Wi-Fi корпоративной сети", Description="Нужен доступ к WiFi")
    assert await scenario.can_handle(task_match) is True

    task_no_match = TaskDTO(Id=202, Name="Настройка VPN", Description="Нужен внешний VPN доступ")
    assert await scenario.can_handle(task_no_match) is False


@pytest.mark.asyncio
async def test_grant_wlan_preconditions_missing_user():
    scenario = GrantWLANScenario()

    # No applicant info
    task_no_user = TaskDTO(Id=203, Name="Подключение к WiFi")
    res = await scenario.validate_preconditions(task_no_user)
    assert res.is_valid is False
    assert "target_user" in res.missing_facts


@pytest.mark.asyncio
async def test_grant_wlan_execute_success(default_policy):
    scenario = GrantWLANScenario()
    task = TaskDTO(
        Id=204,
        Name="Подключение к Wi-Fi",
        ApplicantName="Иванов Иван Иванович",
        entities=ExtractedEntitiesDTO(target_user="ivanov.i"),
    )

    with patch.object(
        scenario,
        "_grant_wlan_sync",
        return_value={
            "sam_account_name": "ivanov.i",
            "user_dn": "CN=Иванов,DC=corporate,DC=loc",
            "group_dn": "CN=WLAN-WORKNET-ALLOW,DC=corporate,DC=loc",
            "already_member": False,
        },
    ):
        exec_res = await scenario.execute(task, default_policy)
    assert exec_res.success is True
    assert exec_res.target_status_id == 3
    assert "ivanov.i" in exec_res.resolution_comment or "Иванов" in exec_res.resolution_comment


# -------------------------------------------------------------
# 3. OfflineHostScenario Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_offline_host_can_handle():
    scenario = OfflineHostScenario()

    task_match = TaskDTO(Id=301, Name="ПК не включается, недоступен", Description="Компьютер WKS-1050 не пингуется")
    assert await scenario.can_handle(task_match) is True

    task_no_match = TaskDTO(Id=302, Name="Не работает интернет", Description="Нет связи с сайтом")
    assert await scenario.can_handle(task_no_match) is False


@pytest.mark.asyncio
async def test_offline_host_preconditions_missing_pc():
    scenario = OfflineHostScenario()

    task_no_pc = TaskDTO(Id=303, Name="ПК недоступен")
    res = await scenario.validate_preconditions(task_no_pc)
    assert res.is_valid is False
    assert "pc_name" in res.missing_facts


# -------------------------------------------------------------
# 4. RAGConsultationScenario Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_rag_consultation_flow(default_policy):
    scenario = RAGConsultationScenario()
    task = TaskDTO(
        Id=401,
        Name="Как настроить почту на телефоне?",
        Description="Подскажите параметры сервера Outlook для смартфона",
    )

    assert await scenario.can_handle(task) is True

    fake_match = KnowledgeSolutionDTO(
        task_id=999,
        original_name="Инструкция по настройке почты",
        problem="Настройка почты на смартфоне",
        solution="Сервер: mail.corporate.loc, порт 993 SSL",
        service_id=10,
        service_name="Почтовые сервисы",
        status_name="Выполнена",
        quality_score=1.0,
        similarity=0.92,
    )

    with (
        patch("core.scenarios.adapters.rag_consultation.get_embedding_vector", return_value=[0.1] * 1024),
        patch("core.scenarios.adapters.rag_consultation.search_hybrid_solutions", return_value=[fake_match]),
    ):

        precond = await scenario.validate_preconditions(task)
        assert precond.is_valid is True

        exec_res = await scenario.execute(task, default_policy)
        assert exec_res.success is True
        assert exec_res.target_status_id == 3
        assert "mail.corporate.loc" in exec_res.resolution_comment
        assert "Тикет #999" in exec_res.technical_note


# -------------------------------------------------------------
# 5. ScenarioRegistry Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_scenario_registry_priority():
    registry = ScenarioRegistry()
    printer_scen = InstallPrinterScenario()
    wlan_scen = GrantWLANScenario()
    rag_scen = RAGConsultationScenario()

    registry.register(printer_scen)
    registry.register(wlan_scen)
    registry.register(rag_scen)

    # Printer takes precedence over RAG
    task_printer = TaskDTO(Id=501, Name="Установка принтера", Description="Подключить HP")
    matched = await registry.find_scenario(task_printer)
    assert matched is not None
    assert matched.scenario_key == "install_printer"

    # WLAN takes precedence over RAG
    task_wlan = TaskDTO(Id=502, Name="Подключение к Wi-Fi", Description="Нужен доступ к беспроводной сети")
    matched_wlan = await registry.find_scenario(task_wlan)
    assert matched_wlan is not None
    assert matched_wlan.scenario_key == "grant_wlan"
