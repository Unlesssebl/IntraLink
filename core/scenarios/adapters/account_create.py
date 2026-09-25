"""Account creation and onboarding autonomous scenario in Active Directory."""

from __future__ import annotations

import logging
from typing import Optional

from core.ad.pool import ActiveDirectoryPool
from core.ad.provisioning import (
    AccountProvisioningError,
    AccountProvisioningService,
)
from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from core.intraservice.service_definition import ServiceDefinition
from core.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("core.scenarios.adapters.account_create")

ACCOUNT_CREATE_SERVICE_IDS = {55, 232}

ACCOUNT_CREATE_PHRASES = (
    "создать учет",
    "создать учёт",
    "создание учет",
    "создание учёт",
    "создать пользователя",
    "создание пользователя",
    "завести пользователя",
    "завести сотрудника",
    "новый пользователь",
    "новый сотрудник",
    "создание уз",
    "создать уз",
    "новая учетная запись",
    "заявка на создание пользователя",
    "заявка на создание учетной записи",
    "заявка на пользователя директум",
    "заявка на пользователя directum",
    "выход сотрудника",
)

ACCOUNT_CREATE_EXCLUSIONS = (
    "сброс парол",
    "сбросить парол",
    "забыл парол",
    "забыла парол",
    "заблокирован",
    "увольнен",
    "блокировк",
    "wlan",
    "wi-fi",
    "wifi",
)


class AccountCreateScenario(BaseScenario):
    """Autonomous Active Directory employee onboarding and account provisioning scenario."""

    scenario_key = "account_create"
    name = "Создание учетной записи Directum / AD"
    description = "Автономное создание учетной записи сотрудника в Active Directory и выдача прав"
    semantic_prototypes = [
        "создать учетку нового сотрудника",
        "выход сотрудника",
        "заявка на пользователя директум",
        "создание учетной записи в Active Directory",
        "завести пользователя в домене",
        "создать учетную запись новому сотруднику",
        "оформить доступ новому сотруднику",
    ]

    definition = ServiceDefinition(
        service_ids=[55, 232],
        name="Создание учетной записи Directum / AD",
        required_facts=["first_name", "last_name", "department", "title"],
        requires_online_host=False,
        clarification_template=(
            "Здравствуйте! Для создания учетной записи пользователя, пожалуйста, укажите обязательные "
            "реквизиты сотрудника: Фамилия, Имя, Подразделение (отдел) и Должность."
        ),
        adapter_key="account_create",
        min_confidence=0.85,
    )

    def __init__(
        self,
        ad_pool: Optional[ActiveDirectoryPool] = None,
        provisioner: Optional[AccountProvisioningService] = None,
    ) -> None:
        self.provisioner = provisioner or AccountProvisioningService(ad_pool=ad_pool)

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket describes an employee onboarding or account creation request."""
        text = f"{task.name or ''} {task.description or ''}".lower()

        # Check explicit exclusions
        if any(ex in text for ex in ACCOUNT_CREATE_EXCLUSIONS):
            return False

        # Service Catalog check (Service 55, 232 or TaskTypeId 1018)
        if task.service_id is not None and task.service_id in ACCOUNT_CREATE_SERVICE_IDS:
            return True

        if task.task_type_id is not None and task.task_type_id == 1018:
            return True

        # Text phrase heuristics
        return any(phrase in text for phrase in ACCOUNT_CREATE_PHRASES)

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate presence of essential identity facts (first_name, last_name, department, title)."""
        missing: list[str] = []

        ent = task.entities
        last_name = (ent.last_name or "").strip()
        first_name = (ent.first_name or "").strip()
        department = (ent.department or "").strip()
        title = (ent.title or "").strip()

        # Fallback extraction from combined user_name if individual fields missing
        if not (last_name and first_name) and ent.user_name:
            parts = ent.user_name.strip().split()
            if len(parts) >= 2:
                last_name = last_name or parts[0]
                first_name = first_name or parts[1]

        if not last_name:
            missing.append("last_name")
        if not first_name:
            missing.append("first_name")
        if not department:
            missing.append("department")
        if not title:
            missing.append("title")

        if missing:
            return PreconditionResult(
                is_valid=False,
                missing_facts=missing,
                clarification_prompt=self.definition.clarification_template,
            )

        return PreconditionResult(is_valid=True)

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Provision AD account and expose only a secret-free receipt."""
        ent = task.entities
        last_name = (ent.last_name or "").strip()
        first_name = (ent.first_name or "").strip()
        middle_name = (ent.middle_name or "").strip()
        department = (ent.department or "").strip()
        title = (ent.title or "").strip()
        phone = (ent.phone or "").strip()
        company = (ent.company or "").strip()

        if not (last_name and first_name) and ent.user_name:
            parts = ent.user_name.strip().split()
            if len(parts) >= 2:
                last_name = last_name or parts[0]
                first_name = first_name or parts[1]

        full_name = f"{last_name} {first_name} {middle_name}".strip()

        logger.info("Executing AccountCreateScenario for ticket #%s: %s", task.id, full_name)

        try:
            receipt = await self.provisioner.provision(
                task_id=task.id,
                last_name=last_name,
                first_name=first_name,
                middle_name=middle_name,
                department=department,
                title=title,
                phone=phone,
                company=company,
            )

            sam = receipt.sam_account_name
            upn = receipt.upn
            user_dn = receipt.user_dn

            # Public comment NEVER contains plaintext password (Zero-Plaintext Policy)
            resolution_comment = (
                f"Здравствуйте! Учетная запись сотрудника {full_name} успешно создана в Active Directory.\n"
                f"• Логин: {sam}\n"
                f"• UPN: {upn}\n"
                f"• Подразделение: {department}\n"
                f"• Должность: {title}\n\n"
                "Логин и временный пароль записаны в соответствующие защищённые поля заявки. "
                "При первом входе в систему потребуется обязательная смена пароля (флаг pwdLastSet=0 установлен)."
            )

            technical_note = (
                f"[Создание учетной записи]\n"
                f"Сотрудник: {full_name}\n"
                f"Логин (sAMAccountName): {sam}\n"
                f"UPN: {upn}\n"
                f"DN: {user_dn}\n"
                f"Подразделение: {department}\n"
                f"Должность: {title}\n"
                "Учётные данные записаны в Field1488/Field1489; пароль не включён в журналы и комментарии.\n"
                f"Статус: Выполнена (Status 3)"
            )

            return ScenarioExecutionResult(
                success=True,
                action_taken="account_create",
                resolution_comment=resolution_comment,
                technical_note=technical_note,
                target_status_id=3,
                metadata={
                    "sam_account_name": sam,
                    "upn": upn,
                    "user_dn": user_dn,
                    "full_name": full_name,
                    "credentials_written": receipt.credentials_written,
                },
            )
        except AccountProvisioningError as exc:
            logger.error("Account provisioning failed for ticket #%s: %s", task.id, exc.code)
            return ScenarioExecutionResult(
                success=False,
                action_taken="account_create",
                resolution_comment="",
                technical_note=(
                    "[Ошибка создания учетной записи]\n"
                    f"Сотрудник: {full_name}\n"
                    f"Подразделение: {department}\n"
                    f"Машинная причина: {exc.code}\n"
                    f"Объект AD создан: {exc.ad_object_created}\n"
                    "Автоматический повтор запрещён; требуется ручная проверка инженером."
                ),
                target_status_id=2,
                error=exc.code,
                metadata={
                    "failure_code": exc.code,
                    "ad_object_created": exc.ad_object_created,
                    "safe_to_retry": False,
                },
            )
        except Exception as exc:
            logger.error("Failed to create AD user for ticket #%s: %s", task.id, exc, exc_info=True)
            return ScenarioExecutionResult(
                success=False,
                action_taken="account_create",
                resolution_comment="",
                technical_note=(
                    f"⚠️ [Автопилот: Ошибка создания учетной записи в Active Directory]\n"
                    f"Сотрудник: {full_name}\n"
                    f"Подразделение: {department}\n"
                    f"Ошибка: {exc}\n"
                    "Заявка передана на ручной разбор дежурному инженеру (Status 2)."
                ),
                target_status_id=2,
                error=str(exc),
            )
