"""Unit tests for printing scenarios: PrinterSpoolerRestartScenario and DefaultPrinterFixScenario."""

from unittest.mock import AsyncMock

import pytest

from core.autopilot.dto import AutopilotPolicyDTO
from core.diagnostic.ports import FastProbeResult, FastSocketProbe
from core.diagnostic.winrm import WinRMExecutor
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.scenarios.adapters.default_printer_fix import DefaultPrinterFixScenario
from core.scenarios.adapters.printer_spooler_restart import PrinterSpoolerRestartScenario


@pytest.fixture
def default_policy() -> AutopilotPolicyDTO:
    return AutopilotPolicyDTO(
        scenario_key="printer_spooler_restart",
        mode="FULL_AUTO",
        min_confidence=0.85,
    )


# --------------------------------------------------------------------------
# 1. PrinterSpoolerRestartScenario Tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spooler_restart_can_handle():
    scenario = PrinterSpoolerRestartScenario()

    # Matches spooler queue stuck
    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=1,
                Name="Зависла печать",
                Description="Документы висят в очереди печати на WKS-01, принтер не реагирует",
            )
        )
        is True
    )

    # Exclusions (printer installation or buying paper)
    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=2,
                Name="Установка принтера",
                Description="Подключить новый принтер в бухгалтерию",
            )
        )
        is False
    )
    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=3,
                Name="Заправка картриджа",
                Description="Купить бумагу и картридж для принтера",
            )
        )
        is False
    )


@pytest.mark.asyncio
async def test_spooler_restart_preconditions_validation():
    mock_probe = AsyncMock(spec=FastSocketProbe)
    scenario = PrinterSpoolerRestartScenario(probe=mock_probe)

    # Missing pc_name
    task_no_pc = TaskDTO(Id=10, Entities=ExtractedEntitiesDTO())
    res_no_pc = await scenario.validate_preconditions(task_no_pc)
    assert res_no_pc.is_valid is False
    assert res_no_pc.missing_facts == ["pc_name"]

    # Host offline
    mock_probe.probe.return_value = FastProbeResult(
        host="WKS-01",
        is_online=False,
        ports={5985: False, 9100: False},
    )
    task_offline = TaskDTO(Id=11, Entities=ExtractedEntitiesDTO(pc_name="WKS-01"))
    res_offline = await scenario.validate_preconditions(task_offline)
    assert res_offline.is_valid is False
    assert "host_offline" in res_offline.environment_barriers

    # WinRM closed
    mock_probe.probe.return_value = FastProbeResult(
        host="WKS-01",
        is_online=True,
        ports={5985: False, 9100: True},
    )
    res_winrm_closed = await scenario.validate_preconditions(task_offline)
    assert res_winrm_closed.is_valid is False
    assert "winrm_closed" in res_winrm_closed.environment_barriers

    # All conditions satisfied
    mock_probe.probe.return_value = FastProbeResult(
        host="WKS-01",
        is_online=True,
        ports={5985: True, 9100: True},
    )
    res_ok = await scenario.validate_preconditions(task_offline)
    assert res_ok.is_valid is True


@pytest.mark.asyncio
async def test_spooler_restart_execution(default_policy):
    mock_winrm = AsyncMock(spec=WinRMExecutor)
    mock_winrm.run_powershell.return_value = (0, "CLEARED:3;STATUS:Running\r\n", "")

    scenario = PrinterSpoolerRestartScenario(winrm_executor=mock_winrm)

    task = TaskDTO(
        Id=201,
        Entities=ExtractedEntitiesDTO(pc_name="WKS-042"),
    )

    res = await scenario.execute(task, default_policy)

    assert res.success is True
    assert res.target_status_id == 3
    assert res.action_taken == "printer_spooler_restart"
    assert res.metadata["cleared_jobs"] == 3
    assert "WKS-042" in res.resolution_comment
    assert "3 шт." in res.resolution_comment
    assert "Running" in res.technical_note

    # Verify error handling when WinRM returns non-zero code
    mock_winrm.run_powershell.return_value = (1, "", "Access denied")
    res_fail = await scenario.execute(task, default_policy)
    assert res_fail.success is False
    assert res_fail.target_status_id == 2
    assert "Access denied" in res_fail.technical_note


# --------------------------------------------------------------------------
# 2. DefaultPrinterFixScenario Tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_default_printer_can_handle():
    scenario = DefaultPrinterFixScenario()

    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=301,
                Name="Слетел принтер",
                Description="Поставить по умолчанию принтер HP LaserJet в кабинете 305",
            )
        )
        is True
    )

    assert (
        await scenario.can_handle(
            TaskDTO(
                Id=302,
                Name="Заправка картриджа",
                Description="Закончился тонер",
            )
        )
        is False
    )


@pytest.mark.asyncio
async def test_default_printer_preconditions_and_execution(default_policy):
    mock_probe = AsyncMock(spec=FastSocketProbe)
    mock_probe.probe.return_value = FastProbeResult(
        host="WKS-007",
        is_online=True,
        ports={5985: True, 9100: True},
    )

    mock_winrm = AsyncMock(spec=WinRMExecutor)
    mock_winrm.run_powershell.return_value = (0, "DEFAULT_SET:Canon MF440\r\n", "")

    scenario = DefaultPrinterFixScenario(probe=mock_probe, winrm_executor=mock_winrm)

    # Missing facts
    task_no_facts = TaskDTO(Id=401, Entities=ExtractedEntitiesDTO())
    res_missing = await scenario.validate_preconditions(task_no_facts)
    assert res_missing.is_valid is False
    assert set(res_missing.missing_facts) == {"pc_name", "printer_address"}

    # Valid task execution
    task_valid = TaskDTO(
        Id=402,
        Entities=ExtractedEntitiesDTO(pc_name="WKS-007", printer_model="Canon MF440"),
    )

    res_prec = await scenario.validate_preconditions(task_valid)
    assert res_prec.is_valid is True

    res_exec = await scenario.execute(task_valid, default_policy)
    assert res_exec.success is True
    assert res_exec.target_status_id == 3
    assert res_exec.action_taken == "default_printer_fix"
    assert "Canon MF440" in res_exec.resolution_comment
    assert "WKS-007" in res_exec.resolution_comment
