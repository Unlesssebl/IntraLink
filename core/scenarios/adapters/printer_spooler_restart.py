"""Printer spooler restart and queue flush autonomous scenario."""

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

logger = logging.getLogger("core.scenarios.adapters.printer_spooler_restart")

PRINTER_SERVICE_IDS = {12, 40}

SPOOLER_PHRASES = (
    "зависла печать",
    "завис документ",
    "зависли документы",
    "очистить очередь",
    "сбросить очередь",
    "сброс очереди",
    "очередь печати",
    "документы висят в очереди",
    "печать не идет",
    "печать не уходит",
    "перезапустить spooler",
    "перезапустить спулер",
    "перезапуск службы печати",
    "диспетчер печати остановлен",
    "служба печати остановлена",
)

SPOOLER_EXCLUSIONS = (
    "установ",
    "подключ",
    "новый принтер",
    "заправить",
    "заправка",
    "картридж",
    "купить",
    "бумаг",
    "замятие",
    "замяло",
    "скрипит",
    "трещит",
    "грязно печатает",
    "полосит",
)


class PrinterSpoolerRestartScenario(BaseScenario):
    """Autonomous workstation printer spooler service restart and queue cleanup scenario."""

    scenario_key = "printer_spooler_restart"
    name = "Перезапуск очереди печати (Spooler)"
    description = "Автономный перезапуск службы печати Spooler и очистка зависшей очереди на удаленном ПК"
    semantic_prototypes = [
        "зависла печать",
        "очистить очередь печати",
        "печать не идет, документы висят в очереди",
        "перезапустить диспетчер печати spooler",
        "застрял документ в очереди принтера",
        "ошибка очереди печати на компьютере",
        "сброс очереди печати",
    ]

    definition = ServiceDefinition(
        service_ids=[12, 40],
        name="Перезапуск очереди печати (Spooler)",
        required_facts=["pc_name"],
        requires_online_host=True,
        probe_ports=[5985, 9100],
        clarification_template=(
            "Здравствуйте! Для перезапуска службы печати и очистки зависшей очереди, пожалуйста, "
            "укажите сетевое имя вашего компьютера (наклейка на системном блоке, например: WKS-042)."
        ),
        adapter_key="printer_spooler_restart",
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
        """Check if ticket describes a stuck printer queue or spooler crash."""
        text = f"{task.name or ''} {task.description or ''}".lower()

        # Reject hardware/consumables/install requests
        if any(ex in text for ex in SPOOLER_EXCLUSIONS):
            return False

        has_device = any(d in text for d in ("принтер", "мфу", "печать", "spooler", "спулер")) or bool(
            task.entities.printer_model or task.entities.printer_address
        )
        has_issue = any(phrase in text for phrase in SPOOLER_PHRASES) or any(
            w in text for w in ("завис", "застрял", "очеред", "висят", "не идет", "не уходит")
        )

        if task.service_id is not None and task.service_id in PRINTER_SERVICE_IDS:
            return has_issue

        return has_device and has_issue

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate pc_name presence and check network/WinRM reachability via FastSocketProbe."""
        pc_name = (task.entities.pc_name or "").strip()
        if not pc_name:
            return PreconditionResult(
                is_valid=False,
                missing_facts=["pc_name"],
                clarification_prompt=self.definition.clarification_template,
            )

        # Fast non-blocking socket probe (timeout <= 1.5s)
        probe_res = await self.probe.probe(pc_name, ports=[5985, 9100], timeout_sec=1.5)
        if not probe_res.is_online:
            return PreconditionResult(
                is_valid=False,
                environment_barriers=["host_offline"],
                clarification_prompt=(
                    f"Здравствуйте! Компьютер {pc_name} в данный момент выключен или недоступен в корпоративной сети. "
                    "Пожалуйста, включите ваш рабочий компьютер и проверьте подключение сетевого кабеля."
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
        """Execute autonomous spooler restart and queue cleanup on the target workstation."""
        pc_name = (task.entities.pc_name or "").strip()
        logger.info("Executing PrinterSpoolerRestartScenario for ticket #%s on host %s", task.id, pc_name)

        ps_script = (
            "Stop-Service -Name Spooler -Force -ErrorAction SilentlyContinue; "
            "$files = Get-ChildItem -Path \"$env:SystemRoot\\System32\\spool\\PRINTERS\\*\" -Force -ErrorAction SilentlyContinue; "
            "$count = ($files | Measure-Object).Count; "
            "Remove-Item -Path \"$env:SystemRoot\\System32\\spool\\PRINTERS\\*\" -Force -Recurse -ErrorAction SilentlyContinue; "
            "Start-Service -Name Spooler -ErrorAction Stop; "
            "$svc = Get-Service -Name Spooler; "
            "Write-Output \"CLEARED:$count;STATUS:$($svc.Status)\""
        )

        status_code, stdout, stderr = await self.winrm_executor.run_powershell(
            target_pc=pc_name,
            script=ps_script,
            timeout_sec=60.0,
        )

        if status_code == 0 and "STATUS:Running" in stdout:
            m_cleared = re.search(r"CLEARED:(\d+)", stdout)
            cleared_count = int(m_cleared.group(1)) if m_cleared else 0

            resolution_comment = (
                f"Здравствуйте! Служба очереди печати (Spooler) на компьютере {pc_name} успешно перезапущена. "
                f"Зависшие задания печати ({cleared_count} шт.) очищены.\n"
                "Пожалуйста, отправьте документ на печать повторно."
            )

            technical_note = (
                f"🤖 [Автопилот: Перезапуск очереди печати]\n"
                f"Хост: {pc_name} (WinRM 5985 OK)\n"
                f"Действие: Очистка очереди spool\\PRINTERS и перезапуск службы Spooler\n"
                f"Результат: Очищено заданий: {cleared_count}, статус службы: Running\n"
                f"Вывод PowerShell: {stdout.strip()}\n"
                f"Статус: Выполнена (Status 3)"
            )

            return ScenarioExecutionResult(
                success=True,
                action_taken="printer_spooler_restart",
                resolution_comment=resolution_comment,
                technical_note=technical_note,
                target_status_id=3,
                metadata={"host": pc_name, "cleared_jobs": cleared_count, "stdout": stdout.strip()},
            )

        error_msg = stderr.strip() or stdout.strip() or f"WinRM exited with code {status_code}"
        logger.error("Spooler restart failed on %s: %s", pc_name, error_msg)

        return ScenarioExecutionResult(
            success=False,
            action_taken="printer_spooler_restart",
            resolution_comment="",
            technical_note=(
                f"⚠️ [Автопилот: Сбой перезапуска очереди печати]\n"
                f"Хост: {pc_name}\n"
                f"Код возврата WinRM: {status_code}\n"
                f"Ошибка: {error_msg}\n"
                "Заявка передана на ручной разбор дежурному инженеру (Status 2)."
            ),
            target_status_id=2,
            error=error_msg,
        )
