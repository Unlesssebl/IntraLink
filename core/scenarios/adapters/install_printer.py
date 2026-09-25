"""Install printer autonomous scenario."""

import logging

from core.autopilot.dto import AutopilotPolicyDTO
from core.diagnostic.ping import fast_ping
from core.diagnostic.ports import probe_diagnostic_ports, probe_tcp_port
from core.diagnostic.printer_probe import PrinterNetworkIdentifier
from core.intraservice.catalog import SERVICE_IDS_PRINTER_INSTALL
from core.intraservice.dto import TaskDTO
from core.intraservice.service_definition import ServiceDefinition
from core.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("core.scenarios.adapters.install_printer")

PRINTER_INSTALL_SERVICE_IDS = SERVICE_IDS_PRINTER_INSTALL


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

    definition = ServiceDefinition(
        service_ids=[62, 82, 83, 183],
        name="Установка принтера",
        required_facts=["pc_name", "printer_address"],
        requires_online_host=True,
        probe_ports=[5985, 9100],
        clarification_template=(
            "Здравствуйте! Для автоматической настройки принтера, пожалуйста, "
            "укажите сетевое имя вашего компьютера (наклейка на системном блоке, например: WKS-042) "
            "и IP-адрес принтера/МФУ."
        ),
        adapter_key="install_printer",
        min_confidence=0.85,
    )

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket describes a printer installation request."""
        text = f"{task.name} {task.description}".lower()

        # Non-printer audio device guard
        audio_tokens = ("наушник", "колонки", "коллонки", "гарнитур", "микрофон")
        printer_tokens = ("принтер", "мфу", "printer", "печать")
        if any(tok in text for tok in audio_tokens) and not any(tok in text for tok in printer_tokens):
            return False

        # Dedicated printer installation service IDs
        if task.service_id is not None and task.service_id in (62, 82, 83):
            return True

        install_tokens = ("установ", "подключ", "добав", "настроить", "переустанов")
        has_device = any(tok in text for tok in printer_tokens) or bool(task.entities.printer_model)
        has_intent = any(tok in text for tok in install_tokens)

        # Spooler restart tokens take precedence over installation
        spooler_tokens = (
            "очередь печати",
            "зависла печать",
            "сбросить очередь",
            "очистить очередь",
            "перезапустить спулер",
            "перезапустить spooler",
            "висят документы",
            "висит печать",
        )
        if any(tok in text for tok in spooler_tokens) and not any(tok in text for tok in install_tokens):
            return False

        # Ignore troubleshooting breakdowns if it's not a fresh install
        failure_tokens = ("замяло", "полосит", "грязно печатает", "скрипит", "трещит")
        if any(tok in text for tok in failure_tokens) and not any(tok in text for tok in install_tokens):
            return False

        return has_device and has_intent

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate network presence, pc_name entity and preflight diagnostic ports.

        Supports two canonical connection branches according to enterprise Helpdesk standards:
        1. Network Printer / MFU: requires online host and printer IP / network queue.
        2. Local USB Printer: requires online host to which USB cable is connected (no IP needed).
        3. Undefined: prompts applicant with Template #7 (Network IP vs USB host).
        """
        missing = []
        barriers = []

        host = (task.entities.pc_name or "").strip()
        printer_addr = (task.entities.printer_address or "").strip()
        full_text = f"{task.name} {task.description or ''}".lower()

        if not host:
            missing.append("pc_name")
            return PreconditionResult(
                is_valid=False,
                missing_facts=missing,
                clarification_prompt=(
                    "Здравствуйте! Для автоматической настройки принтера, пожалуйста, "
                    "укажите сетевое имя вашего компьютера (наклейка на системном блоке, например: KZM0010 или WKS-042)."
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
                    "Не вижу ПК в сети.\n"
                    "1. Убедитесь в корректности имени ПК;\n"
                    "2. Перезагрузите компьютер или включите ПК;\n"
                    "3. Проверьте подключение сетевого кабеля;\n"
                    "4. Если кабель подключен, проверьте наличие световой индикации в месте подключения кабеля.\n"
                    "По вопросам звоните на номер 49-87."
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
                    f"Компьютер {host} доступен по сети (Ping OK), но службы удаленного управления WinRM/SMB заблокированы брандмауэром.\n"
                    "По вопросам звоните на номер 49-87."
                ),
            )

        # Determine connection branch: USB vs Network
        is_usb = any(kw in full_text for kw in ("usb", "юсб", "шнур", "кабел", "провод", "локальн"))

        # Case 1: Connection type is undefined and no printer address provided
        # Enterprise Helpdesk Template #7
        if not is_usb and not printer_addr:
            missing.extend(["printer_address", "printer_connection_type"])
            return PreconditionResult(
                is_valid=False,
                missing_facts=missing,
                clarification_prompt=(
                    "Если принтер сетевой, укажите IP адрес ( указан на самом принтере, в формате 10.244.***.***).\n"
                    "В случае подключения по USB укажите номер ПК, к которому подключен принтер.\n"
                    "По вопросам звоните на номер 49-87."
                ),
            )

        # Case 2: USB connection - no printer IP required, host is verified online
        if is_usb:
            logger.info("USB printer connection branch detected for task #%d on host %s", task.id, host)
            return PreconditionResult(is_valid=True)

        # Case 3: Network printer with explicit address
        if printer_addr:
            port_9100 = await probe_tcp_port(printer_addr, 9100)
            if not port_9100:
                logger.debug("Port 9100 on printer %s unreachable during preflight", printer_addr)

            # Ground-truth network identification of printer model
            if not task.entities.printer_model:
                try:
                    identified_model = await PrinterNetworkIdentifier.identify(printer_addr, timeout_sec=1.5)
                    if identified_model:
                        task.entities.printer_model = identified_model
                        logger.info("Identified printer hardware model for %s: '%s'", printer_addr, identified_model)
                except Exception as exc:
                    logger.debug("Printer network identification error for %s: %s", printer_addr, exc)

        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Refuse to report success until a real Windows executor is available."""
        pc_name = (task.entities.pc_name or "").strip()
        printer_addr = (task.entities.printer_address or "").strip()
        full_text = f"{task.name} {task.description or ''}".lower()
        is_usb = any(kw in full_text for kw in ("usb", "юсб", "шнур", "кабел", "провод", "локальн"))

        printer_target = (printer_addr or task.entities.printer_model or "USB/Network Printer").strip()

        logger.info(
            "Executing InstallPrinterScenario for task #%d on host %s (type: %s, target: %s)",
            task.id,
            pc_name,
            "USB" if is_usb else "Network",
            printer_target,
        )
        connection = "usb" if is_usb else "network"
        error_code = "printer_executor_unavailable"
        return ScenarioExecutionResult(
            success=False,
            action_taken="install_printer",
            resolution_comment="",
            technical_note=(
                "[Установка принтера: действие не выполнено]\n"
                f"Рабочая станция: {pc_name}\n"
                f"Тип подключения: {connection}\n"
                f"Принтер: {printer_target}\n"
                "Причина: Windows-исполнитель и подтверждённая карта драйверов пока недоступны.\n"
                "Заявка оставлена в статусе «В работе» для ручного исполнения."
            ),
            target_status_id=2,
            error=error_code,
            metadata={
                "failure_code": error_code,
                "host": pc_name,
                "printer": printer_target,
                "connection": connection,
                "safe_to_retry": False,
            },
        )
