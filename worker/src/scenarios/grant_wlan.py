"""Corporate Wi-Fi (WLAN-WORKNET) access autonomous scenario."""

import logging

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from worker.src.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("worker.scenarios.grant_wlan")

WLAN_SERVICE_IDS = {63}


class GrantWLANScenario(BaseScenario):
    """Autonomous scenario providing access to corporate WLAN-WORKNET network via AD group membership."""

    scenario_key = "grant_wlan"
    name = "Доступ к Wi-Fi WLAN-WORKNET"
    description = "Предоставление доступа к корпоративной сети Wi-Fi через группу безопасности Active Directory"

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket requests corporate Wi-Fi access."""
        if task.service_id is not None and task.service_id in WLAN_SERVICE_IDS:
            return True

        text = f"{task.name or ''} {task.description or ''}".lower()
        keywords = (
            "wlan-worknet",
            "доступ к wi-fi",
            "доступ к wifi",
            "доступ к вайфай",
            "подключение к wi-fi",
            "подключение к wifi",
            "подключить к wi-fi",
            "подключить ноутбук к wi-fi",
            "корпоративный wi-fi",
            "пароль от wi-fi",
            "вай-фай",
            "вайфай",
        )
        return any(kw in text for kw in keywords)

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate target user identity."""
        target_user = (
            task.entities.target_user
            or task.applicant_name
            or task.creator_name
            or ""
        ).strip()

        if not target_user:
            return PreconditionResult(
                is_valid=False,
                missing_facts=["target_user"],
                clarification_prompt=(
                    "Здравствуйте! Для предоставления доступа к корпоративной сети Wi-Fi (WLAN-WORKNET) "
                    "укажите, пожалуйста, ваш рабочий доменный логин или ФИО сотрудника."
                ),
            )

        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Add user to AD WLAN security group and complete ticket."""
        target_user = (
            task.entities.target_user
            or task.applicant_name
            or task.creator_name
            or "unknown"
        ).strip()

        logger.info("Executing GrantWLANScenario for task #%d (account: %s)", task.id, target_user)
        try:
            from worker.src.tasks.ad_actions import grant_wlan_access_task

            ad_res = await grant_wlan_access_task(sam_account_name=target_user)

            res_comment = (
                f"Здравствуйте! Вашей учетной записи ({target_user}) успешно предоставлен доступ к "
                "защищенной корпоративной беспроводной сети **WLAN-WORKNET**.\n\n"
                "**Инструкция по подключению:**\n"
                "1. В списке доступных беспроводных сетей выберите **WLAN-WORKNET**.\n"
                "2. При запросе учетных данных введите ваш рабочий логин (без домена) и пароль Windows.\n"
                "3. При появлении запроса подтверждения сертификата нажмите **«Подключиться»**.\n\n"
                "Если у вас возникнут сложности с подключением на мобильном устройстве или ноутбуке, пожалуйста, обратитесь в Helpdesk."
            )

            tech_note = (
                f"🤖 [Автопилот: Выдача доступа к Wi-Fi]\n"
                f"Пользователь: {target_user}\n"
                f"Целевая группа AD: WLAN-WORKNET-ALLOW\n"
                f"Статус операции: {ad_res.get('status', 'success')}\n"
                f"Заявка выполнена в режиме {policy.mode}."
            )

            return ScenarioExecutionResult(
                success=True,
                action_taken="grant_wlan_access",
                resolution_comment=res_comment,
                technical_note=tech_note,
                target_status_id=3,  # Выполнена
                metadata={"account": target_user, "group": "WLAN-WORKNET-ALLOW"},
            )

        except Exception as exc:
            logger.exception("Failed to grant WLAN access for task #%d: %s", task.id, exc)
            return ScenarioExecutionResult(
                success=False,
                action_taken="grant_wlan_access",
                resolution_comment=(
                    "Не удалось автоматически предоставить доступ к Wi-Fi. "
                    "Заявка передана на ручную обработку дежурному сетевому инженеру."
                ),
                technical_note=f"🤖 [Автопилот: Ошибка выдачи Wi-Fi]\nИсключение: {exc}",
                target_status_id=2,  # В работе
                error=str(exc),
            )
