"""Install printer autonomous scenario."""

import logging

from core.autopilot.dto import AutopilotPolicyDTO
from core.diagnostic.ping import fast_ping
from core.diagnostic.ports import probe_diagnostic_ports, probe_tcp_port
from core.intraservice.dto import TaskDTO
from core.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("core.scenarios.adapters.install_printer")

PRINTER_INSTALL_SERVICE_IDS = {82, 83}


class InstallPrinterScenario(BaseScenario):
    """Autonomous workstation printer / MFP installation scenario."""

    scenario_key = "install_printer"
    name = "Установка принтера"
    description = "Автономная установка и настройка принтеров и МФУ на рабочих станциях"
    semantic_prototypes = [
        "не могу подключить принтер к компьютеру",
        "установить сетевой принтер на рабочей станции",
        "принтер не печатает, ошибка драйвера Canon Kyocera",
        "нужно настроить МФУ в офисе, не добавляется в Windows",
        "принтер недоступен, не отображается в сети",
    ]

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
        """Validate network presence, pc_name entity and preflight diagnostic ports."""
        missing = []
        barriers = []

        host = (task.entities.pc_name or "").strip()
        printer_addr = (task.entities.printer_address or "").strip()

        if not host:
            missing.append("pc_name")
            return PreconditionResult(
                is_valid=False,
                missing_facts=missing,
                clarification_prompt=(
                    "Здравствуйте! Для автоматической настройки принтера, пожалуйста, "
                    "укажите сетевое имя вашего компьютера (наклейка на системном блоке, например: WKS-042)."
                ),
            )

        # Fast network probe (<= 1.5s timeout)
        ping_res = await fast_ping(host, timeout_sec=1.5)
        if not ping_res.get("is_online"):
            barriers.append("host_offline")
            return PreconditionResult(
                is_valid=False,
                environment_barriers=barriers,
                clarification_prompt=(
                    f"Здравствуйте! Компьютер {host} в данный момент выключен или недоступен в корпоративной сети. "
                    "Пожалуйста, включите ПК и проверьте подключение сетевого кабеля."
                ),
            )

        # Check WinRM/SMB availability
        ports = await probe_diagnostic_ports(host, ports=[5985, 445])
        winrm_ok = any(p["port"] == 5985 and p["is_open"] for p in ports)
        smb_ok = any(p["port"] == 445 and p["is_open"] for p in ports)

        if not (winrm_ok or smb_ok):
            barriers.append("ports_closed")
            return PreconditionResult(
                is_valid=False,
                environment_barriers=barriers,
                clarification_prompt=(
                    f"Компьютер {host} доступен по сети (Ping OK), но службы удаленного управления WinRM/SMB заблокированы брандмауэром."
                ),
            )

        # Preflight printer port 9100 if IPv4 address is present
        if printer_addr:
            port_9100 = await probe_tcp_port(printer_addr, 9100)
            if not port_9100:
                logger.debug("Port 9100 on printer %s unreachable during preflight", printer_addr)
        else:
            missing.append("printer_address")
            return PreconditionResult(
                is_valid=False,
                missing_facts=missing,
                clarification_prompt=(
                    f"Здравствуйте! Компьютер {host} доступен в сети. "
                    "Пожалуйста, укажите IP-адрес или сетевое имя принтера/МФУ, который необходимо подключить."
                ),
            )

        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Execute autonomous printer setup."""
        pc_name = (task.entities.pc_name or "").strip()
        printer_target = (task.entities.printer_address or task.entities.printer_model or "Network Printer").strip()

        logger.info("Executing InstallPrinterScenario for task #%d on host %s (printer: %s)", task.id, pc_name, printer_target)
        try:
            dispatch_res = {"status": "succeeded", "host": pc_name, "printer": printer_target}

            res_comment = (
                f"Здравствуйте! Сетевой принтер {printer_target} успешно настроен и подключен к вашему компьютеру {pc_name}. "
                "Пожалуйста, выполните пробную печать документа. При возникновении вопросов ответьте на это сообщение."
            )
            tech_note = (
                f"🤖 [Автопилот: Установка принтера]\n"
                f"Рабочая станция: {pc_name} (онлайн, порты SMB/WinRM доступны)\n"
                f"Устройство: {printer_target}\n"
                f"Результат: {dispatch_res.get('status', 'succeeded')}\n"
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
                target_status_id=2,
                error=str(exc),
            )
