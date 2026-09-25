"""Offline Host and Hardware Failure autonomous scenario."""

import asyncio
import logging

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from core.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("core.scenarios.adapters.offline_host")

OFFLINE_KEYWORDS = (
    "не включается",
    "не работает компьютер",
    "не включается пк",
    "черный экран",
    "нет питания",
    "компьютер выключен",
    "пк не отвечает",
    "нет сети на компьютере",
    "недоступен компьютер",
    "не загружается windows",
)


async def check_tcp_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """Async check if TCP port is reachable."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


class OfflineHostScenario(BaseScenario):
    """Autonomous scenario diagnosing unreachable workstations and escalating to field engineers (Room 112)."""

    scenario_key = "offline_host"
    name = "Диагностика недоступности ПК"
    description = "Сокетная диагностика и наряд на выезд инженера при физической недоступности рабочего места"
    semantic_prototypes = [
        "компьютер не включается, чёрный экран, нет питания",
        "рабочая станция не реагирует на нажатие кнопки питания",
        "ПК не загружается, гудит кулер но монитор пустой",
        "не могу запустить компьютер в кабинете, запах гари",
        "рабочее место полностью недоступно, компьютер мертвый",
    ]

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket reports a dead or unreachable computer."""
        text = f"{task.name or ''} {task.description or ''}".lower()
        return any(kw in text for kw in OFFLINE_KEYWORDS)

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Ensure workstation name is identified."""
        pc_name = (task.entities.pc_name if task.entities else None) or ""
        if not pc_name:
            return PreconditionResult(
                is_valid=False,
                missing_facts=["pc_name"],
                clarification_prompt=(
                    "Здравствуйте! Для проведения удаленной сетевой диагностики вашего рабочего места "
                    "укажите, пожалуйста, имя компьютера (например, WKS-0123 или WS-...). "
                    "Оно указано на наклейке на системном блоке или в правом нижнем углу экрана блокировки."
                ),
            )

        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Perform network check and dispatch field engineer order."""
        pc_name = (task.entities.pc_name if task.entities else None) or "Unknown-PC"

        smb_online = await check_tcp_port(pc_name, 445, timeout=1.0)
        winrm_online = await check_tcp_port(pc_name, 5985, timeout=1.0)
        is_alive = smb_online or winrm_online

        if is_alive:
            res_comment = (
                f"Здравствуйте! По результатам автоматической диагностики рабочее место **{pc_name}** "
                "отвечает на сетевые запросы (сетевой интерфейс активен).\n\n"
                "Если у вас не отображается картинка на экране:\n"
                "1. Проверьте включение кнопки питания на мониторе (индикатор должен светиться);\n"
                "2. Проверьте плотность подключения кабеля видеосигнала (HDMI / DisplayPort).\n\n"
                "Инженер первой линии проверит доступность системы."
            )
            tech_note = (
                f"🤖 [Автопилот: Диагностика хоста]\n"
                f"Хост: {pc_name} (SMB={smb_online}, WinRM={winrm_online})\n"
                f"Сетевой интерфейс активен. Передано инженеру для уточнения симптомов."
            )
            return ScenarioExecutionResult(
                success=True,
                action_taken="host_diagnostics_online",
                resolution_comment=res_comment,
                technical_note=tech_note,
                target_status_id=2,  # В работе
                metadata={"pc_name": pc_name, "smb_online": smb_online, "winrm_online": winrm_online},
            )
        else:
            res_comment = (
                f"Здравствуйте! Автоматическая система диагностики выявила, что компьютер **{pc_name}** "
                "полностью недоступен по корпоративной сети (нет отклика сетевого адаптера и порта управления).\n\n"
                "Требуется выезд дежурного инженера Helpdesk для проверки кабеля питания, розетки "
                "и коммутационного патч-корда на вашем рабочем месте. Назначение выездной группы выполняет оператор."
            )
            tech_note = (
                f"🤖 [Автопилот: Диагностика хоста - Хост OFFLINE]\n"
                f"Хост: {pc_name} не отвечает (SMB: Unreachable, WinRM: Unreachable).\n"
                f"Рекомендация: требуется выезд инженера; автоматическое назначение выездной группы не выполнялось.\n"
                f"Статус тикета оставлен В работе (2)."
            )
            return ScenarioExecutionResult(
                success=True,
                action_taken="dispatch_field_engineer",
                resolution_comment=res_comment,
                technical_note=tech_note,
                target_status_id=2,  # В работе у человека
                metadata={"pc_name": pc_name, "status": "offline", "field_visit_required": True},
            )
