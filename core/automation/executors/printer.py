"""Printer and host capability executors with explicit verification."""

from __future__ import annotations

import re
from typing import Any

from core.automation.capabilities import (
    CapabilityExecution,
    CapabilityExecutionContext,
    CapabilityOutcome,
    CapabilityPreflight,
    PreflightStatus,
)
from core.diagnostic.ports import FastSocketProbe
from core.diagnostic.winrm import WinRMExecutor, default_winrm_executor


class _WinRMCapability:
    def __init__(
        self,
        probe: FastSocketProbe | None = None,
        winrm_executor: WinRMExecutor | None = None,
    ) -> None:
        self.probe = probe or FastSocketProbe()
        self.winrm_executor = winrm_executor or default_winrm_executor

    async def _host_preflight(self, pc_name: str) -> CapabilityPreflight:
        if not pc_name:
            return CapabilityPreflight(status=PreflightStatus.failed, error_code="missing_pc_name")
        result = await self.probe.probe(pc_name, ports=[5985], timeout_sec=1.5)
        if not result.is_online:
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                checks=["host_probe"],
                details={"pc_name": pc_name, "is_online": False},
                error_code="host_offline",
            )
        if not result.ports.get(5985, False):
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                checks=["host_probe", "winrm_port"],
                details={"pc_name": pc_name, "is_online": True, "winrm_open": False},
                error_code="winrm_unavailable",
            )
        return CapabilityPreflight(
            status=PreflightStatus.passed,
            checks=["host_probe", "winrm_port"],
            details={"pc_name": pc_name, "is_online": True, "winrm_open": True},
        )


class HostProbeExecutor:
    def __init__(self, probe: FastSocketProbe | None = None) -> None:
        self.probe = probe or FastSocketProbe()

    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        if not str(params.get("pc_name", "")).strip():
            return CapabilityPreflight(status=PreflightStatus.failed, error_code="missing_pc_name")
        return CapabilityPreflight(status=PreflightStatus.not_applicable)

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        pc_name = str(params["pc_name"]).strip()
        result = await self.probe.probe(pc_name, ports=[445, 5985], timeout_sec=1.5)
        return CapabilityExecution(
            outcome=CapabilityOutcome.succeeded,
            proof={"pc_name": pc_name, "is_online": result.is_online, "ports": result.ports},
        )


class PrinterProbeExecutor:
    def __init__(self, probe: FastSocketProbe | None = None) -> None:
        self.probe = probe or FastSocketProbe()

    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        if not str(params.get("printer_address", "")).strip():
            return CapabilityPreflight(status=PreflightStatus.failed, error_code="missing_printer_address")
        return CapabilityPreflight(status=PreflightStatus.not_applicable)

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        address = str(params["printer_address"]).strip()
        result = await self.probe.probe(address, ports=[9100], timeout_sec=1.5)
        return CapabilityExecution(
            outcome=CapabilityOutcome.succeeded,
            proof={"printer_address": address, "is_online": result.is_online, "ports": result.ports},
        )


class InstallPrinterExecutor:
    """Fail-closed placeholder until a verified driver-aware executor exists."""

    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        return CapabilityPreflight(
            status=PreflightStatus.failed,
            checks=["executor_availability", "driver_catalog"],
            error_code="printer_executor_unavailable",
        )

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        return CapabilityExecution(
            outcome=CapabilityOutcome.failed,
            error_code="printer_executor_unavailable",
            error_message="Verified printer installation executor is not configured",
        )


class ResetPrintSpoolerExecutor(_WinRMCapability):
    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        return await self._host_preflight(str(params.get("pc_name", "")).strip())

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        pc_name = str(params["pc_name"]).strip()
        script = (
            "Stop-Service -Name Spooler -Force -ErrorAction Stop; "
            "$files = Get-ChildItem -Path \"$env:SystemRoot\\System32\\spool\\PRINTERS\\*\" "
            "-Force -ErrorAction SilentlyContinue; "
            "$count = ($files | Measure-Object).Count; "
            "Remove-Item -Path \"$env:SystemRoot\\System32\\spool\\PRINTERS\\*\" "
            "-Force -Recurse -ErrorAction SilentlyContinue; "
            "Start-Service -Name Spooler -ErrorAction Stop; "
            "$svc = Get-Service -Name Spooler -ErrorAction Stop; "
            "Write-Output \"CLEARED:$count;STATUS:$($svc.Status)\""
        )
        status_code, stdout, stderr = await self.winrm_executor.run_powershell(pc_name, script, timeout_sec=60.0)
        if status_code == 0 and "STATUS:Running" in stdout:
            match = re.search(r"CLEARED:(\d+)", stdout)
            return CapabilityExecution(
                outcome=CapabilityOutcome.succeeded,
                proof={
                    "pc_name": pc_name,
                    "cleared_jobs": int(match.group(1)) if match else 0,
                    "service_status": "Running",
                    "winrm_exit_code": status_code,
                },
            )
        return CapabilityExecution(
            outcome=CapabilityOutcome.failed if status_code >= 0 else CapabilityOutcome.unknown_outcome,
            proof={"pc_name": pc_name, "winrm_exit_code": status_code},
            error_code="spooler_verification_failed",
            error_message=(stderr.strip() or stdout.strip() or "WinRM returned no result"),
        )


class SetDefaultPrinterExecutor(_WinRMCapability):
    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        if not str(params.get("printer_address", "")).strip():
            return CapabilityPreflight(status=PreflightStatus.failed, error_code="missing_printer_address")
        return await self._host_preflight(str(params.get("pc_name", "")).strip())

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        pc_name = str(params["pc_name"]).strip()
        printer_target = str(params["printer_address"]).strip()
        escaped_target = printer_target.replace("'", "''")
        script = (
            f"$target = '{escaped_target}'; "
            "$prn = Get-CimInstance Win32_Printer | Where-Object { "
            "$_.Name -like \"*$target*\" -or $_.PortName -like \"*$target*\" -or $_.ShareName -like \"*$target*\" "
            "} | Select-Object -First 1; "
            "if (-not $prn) { throw \"TARGET_NOT_FOUND:$target\" }; "
            "$network = New-Object -ComObject WScript.Network; $network.SetDefaultPrinter($prn.Name); "
            "$verified = Get-CimInstance Win32_Printer | Where-Object { $_.Default } | Select-Object -First 1; "
            "if (-not $verified -or $verified.Name -ne $prn.Name) { throw \"DEFAULT_VERIFY_FAILED:$($prn.Name)\" }; "
            "Write-Output \"VERIFIED_DEFAULT:$($verified.Name)\""
        )
        status_code, stdout, stderr = await self.winrm_executor.run_powershell(pc_name, script, timeout_sec=60.0)
        match = re.search(r"VERIFIED_DEFAULT:(.+)", stdout)
        if status_code == 0 and match:
            return CapabilityExecution(
                outcome=CapabilityOutcome.succeeded,
                proof={
                    "pc_name": pc_name,
                    "printer": match.group(1).strip(),
                    "verified_default": True,
                    "winrm_exit_code": status_code,
                },
            )
        return CapabilityExecution(
            outcome=CapabilityOutcome.failed if status_code >= 0 else CapabilityOutcome.unknown_outcome,
            proof={"pc_name": pc_name, "printer": printer_target, "winrm_exit_code": status_code},
            error_code="default_printer_verification_failed",
            error_message=(stderr.strip() or stdout.strip() or "WinRM returned no result"),
        )
