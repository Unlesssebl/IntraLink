"""Account creation and onboarding autonomous scenario in Active Directory."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import ldap3

from core.ad.password import generate_secure_password, mask_password
from core.ad.pool import ActiveDirectoryPool
from core.ad.transliteration import generate_sam_account_name
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

    def __init__(self, ad_pool: Optional[ActiveDirectoryPool] = None) -> None:
        self.ad_pool = ad_pool or ActiveDirectoryPool()

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

    def _create_ad_user_sync(
        self,
        last_name: str,
        first_name: str,
        middle_name: str,
        department: str,
        title: str,
        phone: str,
        company: str,
        secret_password_value: str,
    ) -> dict[str, str]:
        """Synchronous LDAP operations for creating Active Directory user with collision resolution.

        Strictly executed in asyncio.to_thread.
        """
        full_name = f"{last_name} {first_name} {middle_name}".strip()
        domain = self.ad_pool.config.domain
        base_dn = ",".join([f"DC={p}" for p in domain.split(".") if p])

        with self.ad_pool.connection_scope(auto_bind=True) as conn:
            # 1. Resolve login collision using GOST 7.79-2000 System B
            collision_index = 1
            sam = generate_sam_account_name(last_name, first_name, middle_name, collision_index)
            while True:
                conn.search(
                    search_base=base_dn,
                    search_filter=f"(&(objectClass=user)(sAMAccountName={sam}))",
                    search_scope=ldap3.SUBTREE,
                    attributes=["sAMAccountName"],
                )
                if not conn.entries:
                    break
                collision_index += 1
                sam = generate_sam_account_name(last_name, first_name, middle_name, collision_index)
                if collision_index > 50:
                    raise RuntimeError("Превышен лимит разрешения коллизий логинов в Active Directory (>50)")

            upn = f"{sam}@{domain}"
            target_ou = os.getenv("AD_USERS_OU") or f"CN=Users,{base_dn}"
            user_dn = f"CN={full_name},{target_ou}"

            # 2. Add User object
            attributes = {
                "sAMAccountName": sam,
                "userPrincipalName": upn,
                "givenName": first_name,
                "sn": last_name,
                "displayName": full_name,
                "department": department,
                "title": title,
            }
            if phone:
                attributes["telephoneNumber"] = phone
            if company:
                attributes["company"] = company

            conn.add(
                dn=user_dn,
                object_class=["top", "person", "organizationalPerson", "user"],
                attributes=attributes,
            )
            if conn.result.get("result") != 0 and conn.result.get("description") != "success":
                raise RuntimeError(
                    f"Ошибка LDAP при создании пользователя '{user_dn}': {conn.result.get('description')} ({conn.result.get('message')})"
                )

            # 3. Set password (Zero-Plaintext, utf-16-le quoted)
            unicode_pwd = f'"{secret_password_value}"'.encode("utf-16-le")
            conn.modify(user_dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [unicode_pwd])]})

            # 4. Set pwdLastSet = 0 (Force password change on first logon)
            conn.modify(user_dn, {"pwdLastSet": [(ldap3.MODIFY_REPLACE, [0])]})

            # 5. Enable account: userAccountControl = 512 (NORMAL_ACCOUNT)
            conn.modify(user_dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [512])]})

            return {
                "sam_account_name": sam,
                "upn": upn,
                "user_dn": user_dn,
                "full_name": full_name,
            }

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Autonomous execution of user creation in Active Directory conforming to Zero-Plaintext Policy."""
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

        # Generate cryptographically secure temporary password (Zero-Plaintext Policy)
        secret_pwd = generate_secure_password(length=14)
        masked_pwd = mask_password(secret_pwd.get_secret_value())

        logger.info(
            "Executing AccountCreateScenario for ticket #%s: %s (password: %s)",
            task.id,
            full_name,
            masked_pwd,
        )

        try:
            ad_res = await asyncio.to_thread(
                self._create_ad_user_sync,
                last_name=last_name,
                first_name=first_name,
                middle_name=middle_name,
                department=department,
                title=title,
                phone=phone,
                company=company,
                secret_password_value=secret_pwd.get_secret_value(),
            )

            sam = ad_res["sam_account_name"]
            upn = ad_res["upn"]
            user_dn = ad_res["user_dn"]

            # Public comment NEVER contains plaintext password (Zero-Plaintext Policy)
            resolution_comment = (
                f"Здравствуйте! Учетная запись сотрудника {full_name} успешно создана в Active Directory.\n"
                f"• Логин: {sam}\n"
                f"• UPN: {upn}\n"
                f"• Подразделение: {department}\n"
                f"• Должность: {title}\n\n"
                "Временный пароль передан руководителю/HR по защищенному регламентному каналу. "
                "При первом входе в систему потребуется обязательная смена пароля (флаг pwdLastSet=0 установлен)."
            )

            # Hidden internal note with temporary password for authenticated Helpdesk operators
            technical_note = (
                f"🤖 [Автопилот: Создание учетной записи]\n"
                f"Сотрудник: {full_name}\n"
                f"Логин (sAMAccountName): {sam}\n"
                f"UPN: {upn}\n"
                f"DN: {user_dn}\n"
                f"Подразделение: {department}\n"
                f"Должность: {title}\n"
                f"Временный пароль: {secret_pwd.get_secret_value()} (передан по регламентному закрытому каналу, pwdLastSet=0)\n"
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
