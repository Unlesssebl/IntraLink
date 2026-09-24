"""Service Redirect and Non-Target Ticket Cancellation Scenario."""

import logging
from typing import Optional, Tuple

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from worker.src.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("worker.scenarios.service_redirect")

# Explicit non-IT services or redirect targets
REDIRECT_PATTERNS = [
    (
        ("directum", "директум", "согласование договора", "служебная записка в directum"),
        "Служба сопровождения Directum",
        "Для вопросов согласования документов, маршрутов Directum и договоров создайте заявку в разделе «Сопровождение Directum» или обратитесь на directum-support@corporate.loc.",
    ),
    (
        ("1с", "1c", "зуп", "бухгалтери", "проводк", "акт сверки"),
        "Группа поддержки 1C",
        "Вопросы работы конфигураций 1C (Бухгалтерия, ЗУП, ERP) обслуживаются отдельной линией поддержки: 1c-help@corporate.loc или по внутреннему номеру 1105.",
    ),
    (
        ("клининг", "уборк", "помыть", "лампочк", "кондиционер", "стул", "стол", "замок", "дверь", "жалюзи"),
        "Административно-хозяйственный отдел (АХО)",
        "Вопросы ремонта мебели, освещения, клининга и микроклимата относятся к ведению АХО. Пожалуйста, оформите заявку в диспетчерскую АХО по телефону 1000.",
    ),
    (
        ("расчетный листок", "отпуск", "справка 2-ндфл", "трудовая книжка"),
        "Отдел кадров и расчетов с персоналом",
        "По вопросам кадровых документов и начисления заработной платы обратитесь в Отдел кадров: hr@corporate.loc.",
    ),
]


class ServiceRedirectScenario(BaseScenario):
    """Autonomous scenario for cancelling non-target tickets and guiding users to appropriate departments."""

    scenario_key = "service_redirect"
    name = "Регламентное перенаправление"
    description = "Регламентная отмена нецелевых обращений со статусом 30 и перенаправление заявителя"

    def _find_redirect_target(self, task: TaskDTO) -> Optional[Tuple[str, str]]:
        text = f"{task.name or ''} {task.description or ''} {task.service_name or ''}".lower()
        for keywords, dept, guidance in REDIRECT_PATTERNS:
            if any(kw in text for kw in keywords):
                return dept, guidance
        return None

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket belongs to a non-IT redirected domain."""
        return self._find_redirect_target(task) is not None

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Preconditions are always valid since redirect only requires ticket text."""
        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Cancel ticket (Status 30) with polite departmental redirect."""
        target = self._find_redirect_target(task)
        dept, guidance = target if target else ("Профильная служба", "Пожалуйста, обратитесь в соответствующее подразделение.")

        logger.info("Executing ServiceRedirectScenario for task #%d to '%s'", task.id, dept)

        res_comment = (
            f"Здравствуйте! Данное обращение относится к компетенции подразделения: **{dept}**.\n\n"
            f"{guidance}\n\n"
            "Текущая заявка в службе технической поддержки IT закрыта (отменена) согласно регламенту маршрутизации."
        )

        tech_note = (
            f"🤖 [Автопилот: Регламентное перенаправление]\n"
            f"Целевое подразделение: {dept}\n"
            f"Регламент: Шлюз релевантности (Фильтр №1)\n"
            f"Статус переведен в 30 (Отменена)."
        )

        return ScenarioExecutionResult(
            success=True,
            action_taken="service_redirect",
            resolution_comment=res_comment,
            technical_note=tech_note,
            target_status_id=30,  # Отменена
            metadata={"target_department": dept},
        )
