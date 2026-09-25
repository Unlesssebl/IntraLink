"""Set default printer autonomous scenario."""

from __future__ import annotations

import logging
import re
from typing import Optional

from core.autopilot.dto import AutopilotPolicyDTO
from core.diagnostic.ports import FastSocketProbe
from core.diagnostic.winrm import WinRMExecutor, default_winrm_executor
from core.intraservice.dto import TaskDTO
from core.intraservice.service_definition import ServiceDefinition
from core.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("core.scenarios.adapters.default_printer_fix")

DEFAULT_PRINTER_SERVICE_IDS = {12, 40}

DEFAULT_PRINTER_PHRASES = (
    "по умолчанию",
    "дефолтн",
    "основной принтер",
    "слетел принтер",
    "не тот принтер",
    "выбрать принтер основным",
    "назначить принтером по умолчанию",
    "сделать принтер дефолтным",
    "поставить по умолчанию",
)

DEFAULT_PRINTER_EXCLUSIONS = (
    "заправить",
    "заправка",
    "картридж",
    "купить",
    "замятие",
    "замяло",
)


class DefaultPrinterFixScenario(BaseScenario):
    """Autonomous workstation default printer configuration scenario."""

    scenario_key = "default_printer_fix"
    name = "Установка принтера по умолчанию"
    description = "Автономная установка принтера по умолчанию в профиле пользователя на удаленном ПК"
    semantic_prototypes = [
        "поставить принтер по умолчанию",
        "назначить основной принтер",
        "слетел принтер по умолчанию",
        "печать отправляется не на тот принтер",
        "сделать принтер дефолтным",
        "выбрать принтер основным",
    ]

    definition = ServiceDefinition(
        service_ids=[12, 40],
        name="Установка принтера по умолчанию",
        required_facts=["pc_name", "printer_address"],
        requires_online_host=True,
        probe_ports=[5985, 9100],
        clarification_template=(
            "Здравствуйте! Для установки принтера по умолчанию, пожалуйста, укажите сетевое имя компьютера "
            "и название (или IP-адрес) нужного принтера."
        ),
        adapter_key="default_printer_fix",
        min_confidence=0.85,
    )

    def __init__(
        self,
        probe: Optional[FastSocketProbe] = None,
        winrm_executor: Optional[WinRMExecutor] = None,
    ) -> None:
        self.probe = probe or FastSocketProbe()
        self.winrm_executor = winrm_executor or default_winrm_executor

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket describes setting or restoring the default printer."""
        text = f"{task.name or ''} {task.description or ''}".lower()

        if any(ex in text for ex in DEFAULT_PRINTER_EXCLUSIONS):
            return False

        has_device = any(d in text for d in ("принтер", "мфу", "печать", "printer")) or bool(
            task.entities.printer_model or task.entities.printer_address
        )
        has_intent = any(phrase in text for phrase in DEFAULT_PRINTER_PHRASES)

        return has_device and has_intent

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate presence of pc_name and printer identifier, plus network readiness."""
        missing: list[str] = []

        pc_name = (task.entities.pc_name or "").strip()
        printer_target = (task.entities.printer_address or task.entities.printer_model or "").strip()

        if not pc_name:
            missing.append("pc_name")
        if not printer_target:
            missing.append("printer_address")

        if missing:
            return PreconditionResult(
                is_valid=False,
                missing_facts=missing,
                clarification_prompt=self.definition.clarification_template,
            )

        # Fast network probe
        probe_res = await self.probe.probe(pc_name, ports=[5985, 9100], timeout_sec=1.5)
        if not probe_res.is_online:
            return PreconditionResult(
                is_valid=False,
                environment_barriers=["host_offline"],
                clarification_prompt=(
                    f"Здравствуйте! Компьютер {pc_name} в данный момент выключен или недоступен по сети. "
                    "Пожалуйста, включите ПК и проверьте сетевое подключение."
                ),
            )

        winrm_open = probe_res.ports.get(5985, False)
        if not winrm_open:
            return PreconditionResult(
                is_valid=False,
                environment_barriers=["winrm_closed"],
                clarification_prompt=(
                    f"Компьютер {pc_name} доступен по сети, но служба удаленного администрирования WinRM (порт 5985) "
                    "заблокирована. Заявка передана на ручное обслуживание дежурному инженеру."
                ),
            )

        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Execute default printer assignment via WinRM on the remote PC."""
        pc_name = (task.entities.pc_name or "").strip()
        printer_target = (task.entities.printer_address or task.entities.printer_model or "").strip()

        logger.info(
            "Executing DefaultPrinterFixScenario for ticket #%s on host %s (printer: %s)",
            task.id,
            pc_name,
            printer_target,
        )

        ps_script = (
            f"$target = '{printer_target}'; "
            "$prn = Get-CimInstance -ClassName Win32_Printer | "
            "Where-Object { $_.Name -like \"*$target*\" -or $_.PortName -like \"*$target*\" -or $_.ShareName -like \"*$target*\" } | "
            "Select-Object -First 1; "
            "if ($prn) { "
            "  $wshNetwork = New-Object -ComObject WScript.Network; "
            "  $wshNetwork.SetDefaultPrinter($prn.Name); "
            "  Write-Output \"DEFAULT_SET:$($prn.Name)\"; "
            "} else { "
            "  $installed = (Get-CimInstance -ClassName Win32_Printer | Select-Object -ExpandProperty Name) -join ', '; "
            "  Write-Error \"Принтер '$target' не найден на $env:COMPUTERNAME. Установленные: $installed\"; "
            "}"
        )

        status_code, stdout, stderr = await self.winrm_executor.run_powershell(
            target_pc=pc_name,
            script=ps_script,
            timeout_sec=60.0,
        )

        if status_code == 0 and "DEFAULT_SET:" in stdout:
            m_set = re.search(r"DEFAULT_SET:(.+)", stdout)
            actual_printer = m_set.group(1).strip() if m_set else printer_target

            resolution_comment = (
                f"Здравствуйте! Принтер {actual_printer} успешно назначен принтером по умолчанию на вашем компьютере {pc_name}."
            )

            technical_note = (
                f"🤖 [Автопилот: Установка принтера по умолчанию]\n"
                f"Хост: {pc_name}\n"
                f"Принтер: {actual_printer}\n"
                f"Вывод PowerShell: {stdout.strip()}\n"
                f"Статус: Выполнена (Status 3)"
            )

            return ScenarioExecutionResult(
                success=True,
                action_taken="default_printer_fix",
                resolution_comment=resolution_comment,
                technical_note=technical_note,
                target_status_id=3,
                metadata={"host": pc_name, "printer": actual_printer, "stdout": stdout.strip()},
            )

        error_msg = stderr.strip() or stdout.strip() or f"WinRM exited with code {status_code}"
        logger.error("Set default printer failed on %s: %s", pc_name, error_msg)

        return ScenarioExecutionResult(
            success=False,
            action_taken="default_printer_fix",
            resolution_comment="",
            technical_note=(
                f"⚠️ [Автопилот: Сбой назначения принтера по умолчанию]\n"
                f"Хост: {pc_name}\n"
                f"Целевой принтер: {printer_target}\n"
                f"Ошибка: {error_msg}\n"
                "Заявка передана на ручной разбор дежурному инженеру (Status 2)."
            ),
            target_status_id=2,
            error=error_msg,
        )
