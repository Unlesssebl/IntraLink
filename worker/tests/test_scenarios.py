"""Unit and integration tests for Core-3 autopilot scenarios."""

from unittest.mock import AsyncMock, patch

import pytest

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.rag.search import KnowledgeSolutionDTO
from worker.src.scenarios.ad_password_reset import ADPasswordResetScenario
from worker.src.scenarios.install_printer import InstallPrinterScenario
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
    assert "укажите, пожалуйста, сетевое имя" in res_no_pc.clarification_prompt

    # Missing Printer address/model
    task_no_printer = TaskDTO(
        Id=104,
        Name="Подключить принтер",
        entities=ExtractedEntitiesDTO(pc_name="WKS-1020"),
    )
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
        patch("worker.src.scenarios.install_printer.probe_diagnostic_ports", return_value={"smb_445": False, "winrm_5985": False}),
        patch("worker.src.scenarios.install_printer.fast_ping", return_value={"host": "WKS-OFFLINE", "is_online": False}),
    ):
        res = await scenario.validate_preconditions(task)
        assert res.is_valid is False
        assert "host_offline" in res.environment_barriers
        assert "Пожалуйста, включите компьютер" in res.clarification_prompt


@pytest.mark.asyncio
async def test_install_printer_execute_success(default_policy):
    scenario = InstallPrinterScenario()
    task = TaskDTO(
        Id=106,
        Name="Установка принтера",
        entities=ExtractedEntitiesDTO(pc_name="WKS-ONLINE", printer_address="192.168.1.100"),
    )

    with patch("worker.src.tasks.install_printer_task" if False else "worker.src.tasks.printers.install_printer_task", new_callable=AsyncMock) as mock_task:
        mock_task.return_value = {"status": "enqueued", "host": "WKS-ONLINE"}
        exec_res = await scenario.execute(task, default_policy)

        assert exec_res.success is True
        assert exec_res.target_status_id == 3
        assert "успешно настроен" in exec_res.resolution_comment
        assert "🤖 [Автопилот: Установка принтера]" in exec_res.technical_note


# -------------------------------------------------------------
# 2. ADPasswordResetScenario Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_ad_password_reset_can_handle():
    scenario = ADPasswordResetScenario()

    task1 = TaskDTO(Id=201, Name="Сброс пароля от учетной записи", Description="Забыл пароль Windows")
    assert await scenario.can_handle(task1) is True

    task2 = TaskDTO(Id=202, Name="Купить картридж", Description="Нужен новый картридж")
    assert await scenario.can_handle(task2) is False


@pytest.mark.asyncio
async def test_ad_password_reset_preconditions():
    scenario = ADPasswordResetScenario()

    task_no_user = TaskDTO(Id=203, Name="Сброс пароля")
    res_no_user = await scenario.validate_preconditions(task_no_user)
    assert res_no_user.is_valid is False
    assert "target_user" in res_no_user.missing_facts

    task_with_user = TaskDTO(
        Id=204,
        Name="Сброс пароля",
        ApplicantName="Иванов И.И.",
        entities=ExtractedEntitiesDTO(target_user="ivanov.i"),
    )
    res_with_user = await scenario.validate_preconditions(task_with_user)
    assert res_with_user.is_valid is True


@pytest.mark.asyncio
async def test_ad_password_reset_execute_success(default_policy):
    scenario = ADPasswordResetScenario()
    task = TaskDTO(
        Id=205,
        Name="Сброс пароля",
        ApplicantName="Сидоров С.С.",
        entities=ExtractedEntitiesDTO(target_user="sidorov.s"),
    )

    with patch("worker.src.tasks.ad_actions.reset_ad_password_task", new_callable=AsyncMock) as mock_ad:
        mock_ad.return_value = {"status": "ok", "account": "sidorov.s"}
        exec_res = await scenario.execute(task, default_policy)

        assert exec_res.success is True
        assert exec_res.target_status_id == 3
        assert "Временный пароль для входа" in exec_res.resolution_comment
        assert "🤖 [Автопилот: Сброс пароля Active Directory]" in exec_res.technical_note


# -------------------------------------------------------------
# 3. RAGConsultationScenario Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_rag_consultation_flow(default_policy):
    scenario = RAGConsultationScenario()
    task = TaskDTO(
        Id=301,
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
        patch("worker.src.scenarios.rag_consultation.get_embedding_vector", return_value=[0.1] * 1024),
        patch("worker.src.scenarios.rag_consultation.search_hybrid_solutions", return_value=[fake_match]),
    ):

        precond = await scenario.validate_preconditions(task)
        assert precond.is_valid is True

        exec_res = await scenario.execute(task, default_policy)
        assert exec_res.success is True
        assert exec_res.target_status_id == 3
        assert "mail.corporate.loc" in exec_res.resolution_comment
        assert "Тикет #999" in exec_res.technical_note


# -------------------------------------------------------------
# 4. ScenarioRegistry Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_scenario_registry_priority():
    registry = ScenarioRegistry()
    printer_scen = InstallPrinterScenario()
    ad_scen = ADPasswordResetScenario()
    rag_scen = RAGConsultationScenario()

    registry.register(printer_scen)
    registry.register(ad_scen)
    registry.register(rag_scen)

    # Printer takes precedence over RAG
    task_printer = TaskDTO(Id=401, Name="Установка принтера", Description="Подключить HP")
    matched = await registry.find_scenario(task_printer)
    assert matched is not None
    assert matched.scenario_key == "install_printer"

    # AD takes precedence over RAG
    task_ad = TaskDTO(Id=402, Name="Сброс пароля AD", Description="Забыл пароль")
    matched_ad = await registry.find_scenario(task_ad)
    assert matched_ad is not None
    assert matched_ad.scenario_key == "ad_password_reset"
