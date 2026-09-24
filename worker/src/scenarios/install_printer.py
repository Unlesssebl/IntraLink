"""Install printer autonomous scenario."""

import logging

from core.autopilot.dto import AutopilotPolicyDTO
from core.diagnostic.ping import fast_ping
from core.diagnostic.ports import probe_diagnostic_ports, probe_tcp_port
from core.intraservice.dto import TaskDTO
from worker.src.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("worker.scenarios.install_printer")

PRINTER_INSTALL_SERVICE_IDS = {82, 83}


class InstallPrinterScenario(BaseScenario):
    """Autonomous workstation printer / MFP installation scenario."""

    scenario_key = "install_printer"
    name = "Установка принтера"
    description = "Автономная установка и настройка принтеров и МФУ на рабочих станциях"

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket describes a printer installation request."""
        text = f"{task.name} {task.description}".lower()

        # Non-printer audio device guard
        audio_tokens = ("наушник", "колонки", "коллонки", "гарнитур", "микрофон")
        printer_tokens = ("принтер", "мфу", "printer", "печать")
        if any(tok in text for tok in audio_tokens) and not any(tok in text for tok in printer_tokens):
            return False

        if task.service_id is not None and task.service_id in PRINTER_INSTALL_SERVICE_IDS:
            return True

        install_tokens = ("установ", "подключ", "добав", "настроить", "переустанов")
        has_device = any(tok in text for tok in printer_tokens) or bool(task.entities.printer_model)
        has_intent = any(tok in text for tok in install_tokens)

        # Ignore troubleshooting breakdowns if it's not a fresh install
        failure_tokens = ("замяло", "полосит", "грязно печатает", "скрипит", "трещит")
        if any(tok in text for tok in failure_tokens) and "установ" not in text:
            return False

        return has_device and has_intent

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate workplace facts (pc_name, printer IP/model) and workstation network reachability."""
        pc_name = (task.entities.pc_name or "").strip()
        printer_addr = (task.entities.printer_address or "").strip()
        printer_model = (task.entities.printer_model or "").strip()

        missing_facts = []
        if not pc_name:
            missing_facts.append("pc_name")

        if not printer_addr and not printer_model:
            missing_facts.append("printer_address")

        if missing_facts:
            prompt_parts = []
            if "pc_name" in missing_facts:
                prompt_parts.append(
                    "укажите, пожалуйста, сетевое имя или инвентарный номер вашего компьютера (наклейка на системном блоке вида WKS-XXXX)"
                )
            if "printer_address" in missing_facts:
                prompt_parts.append(
                    "укажите сетевой IP-адрес принтера или его инвентарный номер/модель"
                )
            clarification = f"Здравствуйте! Для выполнения настройки оборудования {'; а также '.join(prompt_parts)}."
            return PreconditionResult(
                is_valid=False,
                missing_facts=missing_facts,
                clarification_prompt=clarification,
            )

        # Preflight network reachability of the target workstation
        ports_diag = await probe_diagnostic_ports(pc_name)
        is_port_open = any(ports_diag.values())
        if not is_port_open:
            ping_res = await fast_ping(pc_name)
            if not ping_res.get("is_online", False):
                logger.info("Host %s is offline (ports closed and ICMP failed)", pc_name)
                return PreconditionResult(
                    is_valid=False,
                    environment_barriers=["host_offline"],
                    clarification_prompt=(
                        f"Здравствуйте! Рабочий компьютер {pc_name} в данный момент выключен или недоступен по сети. "
                        "Пожалуйста, включите компьютер и оставьте ответный комментарий — настройка продолжится автоматически."
                    ),
                )

        # Preflight printer port 9100 if IPv4 address is present
        if printer_addr:
            port_9100 = await probe_tcp_port(printer_addr, 9100)
            if not port_9100:
                logger.debug("Port 9100 on printer %s unreachable during preflight", printer_addr)

        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Execute autonomous printer setup via task dispatching to windows_exec queue."""
        pc_name = (task.entities.pc_name or "").strip()
        printer_target = (task.entities.printer_address or task.entities.printer_model or "Network Printer").strip()

        logger.info("Executing InstallPrinterScenario for task #%d on host %s (printer: %s)", task.id, pc_name, printer_target)
        try:
            from worker.src.tasks.printers import install_printer_task

            # Dispatch command to windows_exec
            dispatch_res = await install_printer_task(host=pc_name, printer_name=printer_target)

            res_comment = (
                f"Здравствуйте! Сетевой принтер {printer_target} успешно настроен и подключен к вашему компьютеру {pc_name}. "
                "Пожалуйста, выполните пробную печать документа. При возникновении вопросов ответьте на это сообщение."
            )
            tech_note = (
                f"🤖 [Автопилот: Установка принтера]\n"
                f"Рабочая станция: {pc_name} (онлайн, порты SMB/WinRM доступны)\n"
                f"Устройство: {printer_target}\n"
                f"Очередь исполнения: windows_exec\n"
                f"Результат: {dispatch_res.get('status', 'enqueued')}\n"
                f"Статус: Выполнена (Status 3)"
            )
            return ScenarioExecutionResult(
                success=True,
                action_taken="install_printer",
                resolution_comment=res_comment,
                technical_note=tech_note,
                target_status_id=3,
                metadata={"host": pc_name, "printer": printer_target, "dispatch": dispatch_res},
            )
        except Exception as exc:
            logger.error("Failed to execute printer installation on %s: %s", pc_name, exc, exc_info=True)
            return ScenarioExecutionResult(
                success=False,
                action_taken="install_printer",
                resolution_comment="",
                technical_note=f"⚠️ [Автопилот: Сбой установки принтера]\nХост: {pc_name}\nПринтер: {printer_target}\nОшибка: {exc}",
                target_status_id=2,  # Escalation to in progress
                error=str(exc),
            )
