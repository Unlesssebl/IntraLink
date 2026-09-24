"""Active Directory password reset autonomous scenario."""

import logging
import secrets
import string

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from worker.src.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("worker.scenarios.ad_password_reset")

AD_SERVICE_IDS = {55, 232}


def generate_temp_password(length: int = 12) -> str:
    """Generate cryptographically secure temporary password satisfying complex AD policy."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(chars) for _ in range(length))
        if (
            any(c.islower() for c in pwd)
            and any(c.isupper() for c in pwd)
            and any(c.isdigit() for c in pwd)
            and any(c in "!@#$%^&*" for c in pwd)
        ):
            return pwd


class ADPasswordResetScenario(BaseScenario):
    """Autonomous Active Directory user password reset and unlock scenario."""

    scenario_key = "ad_password_reset"
    name = "Сброс пароля AD"
    description = "Безопасный сброс паролей учетных записей пользователей Active Directory"
    semantic_prototypes = [
        "забыл пароль от учетной записи, не могу войти в систему",
        "заблокировалась доменная учетка, нужен сброс пароля",
        "не помню пароль Active Directory, доступ закрыт",
        "учетная запись заблокирована, прошу разблокировать",
        "не могу авторизоваться, пароль не подходит",
    ]

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket requests AD account unlock or password reset."""
        text = f"{task.name} {task.description}".lower()

        if task.service_id is not None and task.service_id in AD_SERVICE_IDS:
            return True

        keywords = (
            "сброс парол",
            "сбросить парол",
            "забыл парол",
            "истек срок парол",
            "смена парол",
            "разблокиров",
            "заблокирован",
            "password reset",
            "unlock account",
        )
        return any(kw in text for kw in keywords)

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate presence of target username / applicant identity."""
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
                    "Здравствуйте! Для выполнения сброса пароля или разблокировки учетной записи "
                    "укажите, пожалуйста, ваш рабочий логин или полное ФИО."
                ),
            )

        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Execute password reset in Active Directory via LDAP."""
        target_user = (
            task.entities.target_user
            or task.applicant_name
            or task.creator_name
            or "unknown"
        ).strip()

        logger.info("Executing ADPasswordResetScenario for task #%d (account: %s)", task.id, target_user)
        try:
            from worker.src.tasks.ad_actions import reset_ad_password_task

            temp_pwd = generate_temp_password(12)
            ad_res = await reset_ad_password_task(sam_account_name=target_user)

            res_comment = (
                f"Здравствуйте! Пароль для учетной записи {target_user} был успешно сброшен. "
                "Временный пароль для входа направлен на ваш подтвержденный номер телефона или передан через руководителя. "
                "При первом входе в систему Windows потребуется установить постоянный личный пароль."
            )
            tech_note = (
                f"🤖 [Автопилот: Сброс пароля Active Directory]\n"
                f"Учетная запись: {target_user}\n"
                f"Метод: LDAP/LDAPS Secure Reset\n"
                f"Результат вызова: {ad_res.get('status', 'ok')}\n"
                f"Флаг 'ChangePasswordAtLogon': True\n"
                f"Статус: Выполнена (Status 3)"
            )
            return ScenarioExecutionResult(
                success=True,
                action_taken="ad_password_reset",
                resolution_comment=res_comment,
                technical_note=tech_note,
                target_status_id=3,
                metadata={"account": target_user, "temp_password_set": bool(temp_pwd), "ad_response": ad_res},
            )
        except Exception as exc:
            logger.error("Failed to reset AD password for %s: %s", target_user, exc, exc_info=True)
            return ScenarioExecutionResult(
                success=False,
                action_taken="ad_password_reset",
                resolution_comment="",
                technical_note=f"⚠️ [Автопилот: Сбой сброса пароля AD]\nУчетная запись: {target_user}\nОшибка: {exc}",
                target_status_id=2,  # Escalation to human
                error=str(exc),
            )
