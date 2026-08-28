"""
Модуль удаленной установки и диагностики принтеров (WinRM, SMB, WMI).
"""
import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

KB_PATH = Path(__file__).resolve().parent.parent / "knowledge_base" / "printers_knowledge_base.json"


@dataclass
class PrinterExecutionResult:
    success: bool
    target_pc: str
    printer_name: str
    message: str
    error: Optional[str] = None


class PrinterExecutor:
    """
    Исполнитель для удаленной диагностики и установки принтеров на рабочих станциях.
    """

    def __init__(self, kb_path: Path = KB_PATH):
        self.kb_path = kb_path
        self._kb_data = None

    def _load_kb(self) -> dict[str, Any]:
        if self._kb_data is None:
            if self.kb_path.exists():
                try:
                    with open(self.kb_path, "r", encoding="utf-8") as f:
                        self._kb_data = json.load(f)
                except Exception as e:
                    logger.error("Ошибка загрузки базы знаний принтеров: %s", e)
                    self._kb_data = {}
            else:
                self._kb_data = {}
        return self._kb_data

    @staticmethod
    def run_remote_powershell(target_pc: str, script: str, timeout: int = 40) -> dict[str, Any]:
        """
        Выполняет PowerShell скрипт на удаленной машине через WinRM / WMI.
        """
        ps_wrapper = f"""
        $ErrorActionPreference = 'Stop'
        try {{
            $res = Invoke-Command -ComputerName "{target_pc}" -ScriptBlock {{
                {script}
            }} -ErrorAction Stop
            Write-Output (ConvertTo-Json @{{ success = $true; data = $res }})
        }} catch {{
            Write-Output (ConvertTo-Json @{{ success = $false; error = $_.Exception.Message }})
        }}
        """
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_wrapper]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
            if res.returncode != 0:
                return {"success": False, "error": res.stderr.strip()}
            out = res.stdout.strip()
            if out:
                json_start = out.find("{")
                json_end = out.rfind("}")
                if json_start != -1 and json_end != -1:
                    return json.loads(out[json_start : json_end + 1])
            return {"success": True, "raw": out}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def check_printer_installed(self, target_pc: str, printer_name: str) -> bool:
        """
        Проверяет, установлен ли принтер на удаленной рабочей станции.
        """
        script = f"Get-Printer -Name '*{printer_name}*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name"
        res = self.run_remote_powershell(target_pc, script)
        return bool(res.get("success") and res.get("data"))

    def install_network_printer(
        self,
        target_pc: str,
        printer_ip: str,
        driver_name: str,
        printer_name: str,
    ) -> PrinterExecutionResult:
        """
        Удаленная установка сетевого принтера (создание TCP/IP порта + добавление очереди).
        """
        port_name = f"IP_{printer_ip}"
        script = f"""
        # 1. Создание TCP/IP порта
        if (-not (Get-PrinterPort -Name '{port_name}' -ErrorAction SilentlyContinue)) {{
            Add-PrinterPort -Name '{port_name}' -PrinterHostAddress '{printer_ip}'
        }}
        # 2. Добавление принтера
        if (-not (Get-Printer -Name '{printer_name}' -ErrorAction SilentlyContinue)) {{
            Add-Printer -Name '{printer_name}' -PortName '{port_name}' -DriverName '{driver_name}'
        }}
        Get-Printer -Name '{printer_name}' | Select-Object Name, PortName, DriverName
        """
        res = self.run_remote_powershell(target_pc, script)
        if res.get("success"):
            return PrinterExecutionResult(
                success=True,
                target_pc=target_pc,
                printer_name=printer_name,
                message=f"Принтер '{printer_name}' успешно установлен на {target_pc}",
            )
        return PrinterExecutionResult(
            success=False,
            target_pc=target_pc,
            printer_name=printer_name,
            message=f"Ошибка установки принтера: {res.get('error')}",
            error=res.get("error"),
        )
