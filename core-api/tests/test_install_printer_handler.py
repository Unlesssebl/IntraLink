"""
Тесты для эталонного InstallPrinterHandler (Инкремент 4).
Проверяет:
- Полный жизненный цикл установки принтера (Happy Path E2E);
- Запрет Generic Fallback при отсутствии профиля в KB (Fail-Fast в Preflight);
- Проверку доверенных корней SMB-хранилищ (Allowlist SMB Roots);
- Read-only префлайт и WMI bootstrap WinRM строго в prepare;
- Безопасный откат службы WinRM в cleanup только при доказанном владении;
- Обработку несовпадения SHA-256 драйвера (driver_hash_mismatch) и зачистку стейджинга;
- Точную верификацию очереди, порта и драйвера в verify.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

# Добавляем execution-worker в sys.path
WORKER_DIR = Path(__file__).resolve().parent.parent.parent / "execution-worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from handlers.install_printer import (
    ALLOWLIST_SMB_ROOTS,
    InstallPrinterHandler,
    InstallPrinterInput,
)
from sdk.models import ExecutionPhase, HandlerContext
from sdk.powershell import PowerShellResult
from shared.printers import PrinterConfig


def _make_ctx(command_id: str = "cmd-print-001", target_pc: str = "WS-TEST-01") -> HandlerContext:
    return HandlerContext(
        command_id=command_id,
        task_id=101,
        target_node=target_pc,
        node_name="test_worker_node",
        claim_token="claim-token-xyz",
    )


@pytest.fixture
def sample_printer_cfg() -> PrinterConfig:
    return PrinterConfig(
        model_key="hp_m402",
        display_name="HP LaserJet M402dn",
        driver_name="HP LaserJet Pro M402-M403 n-dn PCL 6",
        driver_inf_path=r"\\truenas\Drivers\Printers\HP_M402\hp_m402.inf",
        vendor="hp",
        driver_bundle="hp_m402_bundle",
        connection_type="tcpip",
    )


@pytest.mark.asyncio
async def test_validate_success():
    handler = InstallPrinterHandler()
    ctx = _make_ctx()
    input_data = {
        "pc_name": "ws-office-05",
        "printer_name": "HP LaserJet M402dn",
        "connection_type": "tcpip",
        "printer_ip": "192.168.1.50",
    }
    params = handler.input_model.model_validate(input_data)
    ok, msg = await handler.validate(ctx, params)
    assert ok is True
    assert params.pc_name in ("WS-OFFICE-05", "WSOFFICE05")


@pytest.mark.asyncio
async def test_validate_invalid_pc_name():
    with pytest.raises(ValidationError):
        InstallPrinterInput(
            pc_name="invalid..pc..name!!",
            printer_name="HP LaserJet",
        )


@pytest.mark.asyncio
async def test_preflight_fails_when_host_unreachable():
    handler = InstallPrinterHandler()
    ctx = _make_ctx()
    params = InstallPrinterInput(pc_name="WS-DOWN", printer_name="HP LaserJet M402dn")

    with patch("handlers.install_printer.check_tcp_port", AsyncMock(return_value=False)):
        res = await handler.run_pipeline(ctx, params.model_dump())
        assert res.success is False
        assert res.failure_kind == "preflight_failed"
        assert res.failure_code == "host_unreachable"
        assert "недоступна по сети" in res.message


@pytest.mark.asyncio
async def test_preflight_fails_fast_on_unknown_printer_generic_fallback_disabled():
    """Generic fallback строго запрещен: неизвестный принтер отклоняется до любых изменений хоста."""
    handler = InstallPrinterHandler()
    ctx = _make_ctx()
    params = InstallPrinterInput(pc_name="WS-ONLINE", printer_name="Unknown SuperPrinter 9999")

    with patch("handlers.install_printer.check_tcp_port", AsyncMock(return_value=True)), \
         patch("handlers.install_printer.find_printer_by_name", return_value=None):
        res = await handler.run_pipeline(ctx, params.model_dump())
        assert res.success is False
        assert res.failure_kind == "preflight_failed"
        assert res.failure_code == "driver_profile_not_found"
        assert "Generic Fallback disabled" in res.message


@pytest.mark.asyncio
async def test_preflight_fails_on_untrusted_smb_driver_source(sample_printer_cfg: PrinterConfig):
    """Путь к драйверу вне ALLOWLIST_SMB_ROOTS блокируется политикой безопасности."""
    handler = InstallPrinterHandler()
    ctx = _make_ctx()
    bad_cfg = PrinterConfig(
        model_key="bad_model",
        display_name="Bad Printer",
        driver_name="Bad Driver",
        driver_inf_path=r"\\evil-server\share\hack.inf",
        vendor="generic",
        connection_type="tcpip",
    )
    params = InstallPrinterInput(pc_name="WS-ONLINE", printer_name="Bad Printer")

    with patch("handlers.install_printer.check_tcp_port", AsyncMock(return_value=True)), \
         patch("handlers.install_printer.find_printer_by_name", return_value=bad_cfg):
        res = await handler.run_pipeline(ctx, params.model_dump())
        assert res.success is False
        assert res.failure_kind == "preflight_failed"
        assert res.failure_code == "untrusted_driver_source"


@pytest.mark.asyncio
async def test_happy_path_e2e_install_and_verify(sample_printer_cfg: PrinterConfig):
    """Полный счастливый путь: preflight, prepare, execute, verify и cleanup."""
    handler = InstallPrinterHandler()
    ctx = _make_ctx(command_id="cmd-happy-path")
    params = InstallPrinterInput(
        pc_name="WS-CORP-10",
        printer_name="HP LaserJet M402dn",
        connection_type="tcpip",
        printer_ip="192.168.10.150",
    )

    mock_ps_exec = PowerShellResult(
        success=True,
        stdout="Installed OK",
        data={
            "Name": "HP LaserJet M402dn",
            "PortName": "IP_192.168.10.150",
            "DriverName": sample_printer_cfg.driver_name,
            "PrinterStatus": 0,
        },
    )
    mock_ps_verify = PowerShellResult(
        success=True,
        stdout="Verified OK",
        data={
            "Name": "HP LaserJet M402dn",
            "PortName": "IP_192.168.10.150",
            "DriverName": sample_printer_cfg.driver_name,
            "PrinterStatus": 0,
        },
    )
    mock_ps_cleanup = PowerShellResult(success=True, stdout="Cleaned")

    def mock_run_ps(*args, script="", **kwargs):
        if "Get-Printer -Name $printerName" in script and "Get-PrinterPort" not in script:
            return mock_ps_verify
        if "Remove-Item" in script:
            return mock_ps_cleanup
        return mock_ps_exec

    with patch("handlers.install_printer.check_tcp_port", AsyncMock(return_value=True)), \
         patch("handlers.install_printer.find_printer_by_name", return_value=sample_printer_cfg), \
         patch.object(handler, "_check_service_status", AsyncMock(return_value=("Running", "Auto"))), \
         patch("handlers.install_printer.run_powershell_safe", AsyncMock(side_effect=mock_run_ps)):

        res = await handler.run_pipeline(ctx, params.model_dump())

        assert res.success is True
        assert res.failure_kind is None
        assert "верифицирован" in res.message
        assert res.payload is not None
        assert res.payload.get("verified") is True
        assert res.payload.get("installed") is True
        assert res.payload.get("port_name") == "IP_192.168.10.150"
        assert res.payload.get("driver_name") == sample_printer_cfg.driver_name


@pytest.mark.asyncio
async def test_winrm_bootstrap_and_cleanup_restore(sample_printer_cfg: PrinterConfig):
    """
    Если WinRM был остановлен, воркер запускает его в prepare
    и восстанавливает в cleanup с доказанным владением ownership_token.
    """
    handler = InstallPrinterHandler()
    ctx = _make_ctx(command_id="cmd-bootstrap-test")
    params = InstallPrinterInput(
        pc_name="WS-STOPPED-WINRM",
        printer_name="HP LaserJet M402dn",
        printer_ip="192.168.1.100",
    )

    mock_ps = PowerShellResult(
        success=True,
        data={
            "Name": "HP LaserJet M402dn",
            "PortName": "IP_192.168.1.100",
            "DriverName": sample_printer_cfg.driver_name,
        },
    )

    mock_bootstrap = AsyncMock(return_value=True)
    mock_restore = AsyncMock()

    with patch("handlers.install_printer.check_tcp_port", AsyncMock(side_effect=[True, False, True, True])), \
         patch("handlers.install_printer.find_printer_by_name", return_value=sample_printer_cfg), \
         patch.object(handler, "_check_service_status", AsyncMock(return_value=("Stopped", "Manual"))), \
         patch.object(handler, "_bootstrap_start_winrm", mock_bootstrap), \
         patch.object(handler, "_restore_winrm_service", mock_restore), \
         patch("handlers.install_printer.run_powershell_safe", AsyncMock(return_value=mock_ps)):

        res = await handler.run_pipeline(ctx, params.model_dump())

        assert res.success is True
        # Проверяем, что bootstrap был вызван в prepare
        mock_bootstrap.assert_awaited_once()
        # Проверяем, что откат службы был вызван в cleanup
        mock_restore.assert_awaited_once()
        args, _ = mock_restore.call_args
        assert args[0] == "WS-STOPPED-WINRM"
        assert args[1] == "Manual"


@pytest.mark.asyncio
async def test_driver_hash_mismatch_fails_execute_and_cleans_up(sample_printer_cfg: PrinterConfig):
    """При несовпадении SHA-256 драйвера команда падает с driver_hash_mismatch и зачищает стейджинг."""
    handler = InstallPrinterHandler()
    ctx = _make_ctx(command_id="cmd-hash-mismatch")
    params = InstallPrinterInput(
        pc_name="WS-CORP-10",
        printer_name="HP LaserJet M402dn",
        printer_ip="192.168.10.150",
    )

    ps_hash_err = PowerShellResult(
        success=False,
        error="driver_hash_mismatch: expected aaaa, got bbbb",
    )

    def mock_ps(*args, script="", **kwargs):
        if "Remove-Item" in script:
            return PowerShellResult(success=True)
        return ps_hash_err

    with patch("handlers.install_printer.check_tcp_port", AsyncMock(return_value=True)), \
         patch("handlers.install_printer.find_printer_by_name", return_value=sample_printer_cfg), \
         patch.object(handler, "_check_service_status", AsyncMock(return_value=("Running", "Auto"))), \
         patch("handlers.install_printer.run_powershell_safe", AsyncMock(side_effect=mock_ps)):

        res = await handler.run_pipeline(ctx, params.model_dump())

        assert res.success is False
        assert res.failure_code == "driver_hash_mismatch"
        assert "driver_hash_mismatch" in res.message


@pytest.mark.asyncio
async def test_verify_mismatch_detected(sample_printer_cfg: PrinterConfig):
    """Если после установки Get-Printer возвращает другой драйвер, верификация падает с verified_failure=True."""
    handler = InstallPrinterHandler()
    ctx = _make_ctx(command_id="cmd-verify-mismatch")
    params = InstallPrinterInput(
        pc_name="WS-CORP-10",
        printer_name="HP LaserJet M402dn",
        printer_ip="192.168.10.150",
    )

    mock_ps_exec = PowerShellResult(success=True, data={"Name": "HP LaserJet M402dn"})
    # Неверный драйвер при проверке
    mock_ps_verify = PowerShellResult(
        success=True,
        data={
            "Name": "HP LaserJet M402dn",
            "PortName": "IP_192.168.10.150",
            "DriverName": "Generic / Text Only",
        },
    )

    def mock_run_ps(*args, script="", **kwargs):
        if "Get-Printer -Name $printerName" in script and "Get-PrinterPort" not in script:
            return mock_ps_verify
        return mock_ps_exec

    with patch("handlers.install_printer.check_tcp_port", AsyncMock(return_value=True)), \
         patch("handlers.install_printer.find_printer_by_name", return_value=sample_printer_cfg), \
         patch.object(handler, "_check_service_status", AsyncMock(return_value=("Running", "Auto"))), \
         patch("handlers.install_printer.run_powershell_safe", AsyncMock(side_effect=mock_run_ps)):

        res = await handler.run_pipeline(ctx, params.model_dump())

        assert res.success is False
        assert res.failure_kind == "verification_failed"
        assert res.failure_code == "printer_driver_mismatch"
        assert res.verified_failure is True
        assert "Несовпадение драйвера печати" in res.message
