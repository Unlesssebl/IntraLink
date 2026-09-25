"""Account locking and offboarding autonomous scenario in Active Directory."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import ldap3
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from core.ad.pool import ActiveDirectoryPool
from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from core.intraservice.service_definition import ServiceDefinition
from core.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("core.scenarios.adapters.account_lock")

ACCOUNT_LOCK_SERVICE_IDS = {8}

ACCOUNT_LOCK_PHRASES = (
    "увольнен",
    "уволен",
    "уволить",
    "заблокировать учет",
    "заблокировать учёт",
    "заблокировать пользовател",
    "блокировка учет",
    "блокировка учёт",
    "блокировка пользовател",
    "закрыть доступ",
    "отозвать доступ",
    "отключить учет",
    "отключить учёт",
    "отключить пользовател",
    "заблокировать уз",
    "блокировка уз",
    "заблокировать логин",
)

ACCOUNT_LOCK_EXCLUSIONS = (
    "создать",
    "создание",
    "новый пользователь",
    "новый сотрудник",
    "сброс парол",
    "сбросить парол",
    "wlan",
    "wi-fi",
    "wifi",
)


class AccountLockScenario(BaseScenario):
    """Autonomous Active Directory employee offboarding and account locking scenario."""

    scenario_key = "account_lock"
    name = "Блокировка учетной записи (Увольнение)"
    description = "Автономная блокировка учетной записи сотрудника в Active Directory в связи с увольнением"
    semantic_prototypes = [
        "увольнение сотрудника",
        "заблокировать учетную запись",
        "закрыть доступы",
        "отключить учетную запись в AD",
        "блокировка пользователя в связи с увольнением",
        "заблокировать логин сотрудника",
        "деактивировать учетную запись уволенного",
    ]

    definition = ServiceDefinition(
        service_ids=[55, 8],
        name="Блокировка учетной записи (Увольнение)",
        required_facts=["target_user"],
        requires_online_host=False,
        clarification_template=(
            "Здравствуйте! Для блокировки учетной записи, пожалуйста, укажите логин (sAMAccountName) "
            "или точное ФИО увольняемого сотрудника."
        ),
        adapter_key="account_lock",
        min_confidence=0.85,
    )

    def __init__(self, ad_pool: Optional[ActiveDirectoryPool] = None) -> None:
        self.ad_pool = ad_pool or ActiveDirectoryPool()

    async def can_handle(self, task: TaskDTO) -> bool:
        """Check if ticket describes an employee termination or account lockout request."""
        text = f"{task.name or ''} {task.description or ''}".lower()

        # Check explicit exclusions
        if any(ex in text for ex in ACCOUNT_LOCK_EXCLUSIONS):
            return False

        # Service Catalog check (Service 8 = Offboarding/Access revoke)
        if task.service_id is not None and task.service_id in ACCOUNT_LOCK_SERVICE_IDS:
            return True

        # Text phrase heuristics
        return any(phrase in text for phrase in ACCOUNT_LOCK_PHRASES)

    async def validate_preconditions(self, task: TaskDTO) -> PreconditionResult:
        """Validate target_user presence and check initiator authorization."""
        target_user = (task.entities.target_user or "").strip()
        if not target_user and task.entities.user_name:
            target_user = task.entities.user_name.strip()

        if not target_user:
            return PreconditionResult(
                is_valid=False,
                missing_facts=["target_user"],
                clarification_prompt=self.definition.clarification_template,
            )

        # Authorization guard: A user cannot request disabling their own account without manager/HR context
        applicant = (task.applicant_name or task.creator_name or "").lower().strip()
        target_lower = target_user.lower()
        if applicant and (applicant == target_lower or target_lower in applicant):
            return PreconditionResult(
                is_valid=False,
                environment_barriers=["unauthorized_initiator"],
                clarification_prompt=(
                    "Здравствуйте! Заявка на блокировку учетной записи в связи с увольнением должна "
                    "исходить от руководителя подразделения или службы управления персоналом (HR/ИБ)."
                ),
            )

        return PreconditionResult(is_valid=True)

    def _lock_ad_user_sync(self, target_user: str) -> dict[str, str | int]:
        """Synchronously locate user in AD, set ACCOUNTDISABLE flag (0x0002) and optionally relocate to Disabled OU.

        Strictly executed in asyncio.to_thread.
        """
        domain = self.ad_pool.config.domain
        base_dn = ",".join([f"DC={p}" for p in domain.split(".") if p])

        with self.ad_pool.connection_scope(auto_bind=True) as conn:
            # Search by sAMAccountName, displayName or CN. User input must
            # never be interpolated into an LDAP filter without escaping.
            escaped_target = escape_filter_chars(target_user)
            search_filter = (
                f"(&(objectClass=user)(|(sAMAccountName={escaped_target})"
                f"(displayName={escaped_target})(cn={escaped_target})))"
            )
            conn.search(
                search_base=base_dn,
                search_filter=search_filter,
                search_scope=ldap3.SUBTREE,
                attributes=["sAMAccountName", "distinguishedName", "userAccountControl", "displayName", "cn"],
            )

            if len(conn.entries) == 0:
                raise RuntimeError(f"Пользователь '{target_user}' не найден в Active Directory ({domain})")
            if len(conn.entries) != 1:
                raise RuntimeError(
                    f"Пользователь '{target_user}' найден неоднозначно: совпадений {len(conn.entries)}"
                )

            entry = conn.entries[0]
            user_dn = str(entry.distinguishedName.value)
            current_uac = int(entry.userAccountControl.value) if entry.userAccountControl else 512

            # Set ACCOUNTDISABLE bit (0x0002)
            new_uac = current_uac | 0x0002
            conn.modify(user_dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [new_uac])]})

            if conn.result.get("result") != 0 and conn.result.get("description") != "success":
                raise RuntimeError(
                    f"Ошибка LDAP при изменении userAccountControl для '{user_dn}': {conn.result.get('description')}"
                )

            # Move to Disabled OU if configured
            disabled_ou = os.getenv("AD_DISABLED_OU")
            moved = False
            if disabled_ou:
                rdn = f"CN={escape_rdn(str(entry.cn.value or entry.sAMAccountName.value))}"
                try:
                    conn.modify_dn(user_dn, rdn, new_superior=disabled_ou)
                    if conn.result.get("result") == 0 or conn.result.get("description") == "success":
                        moved = True
                        user_dn = f"{rdn},{disabled_ou}"
                    else:
                        logger.warning(
                            "Failed to move disabled user to '%s': %s",
                            disabled_ou,
                            conn.result.get("description"),
                        )
                except Exception as exc:
                    logger.warning("Failed to move disabled user to '%s': %s", disabled_ou, exc)

            conn.search(
                search_base=user_dn,
                search_filter="(objectClass=user)",
                search_scope=ldap3.BASE,
                attributes=["userAccountControl", "sAMAccountName", "distinguishedName"],
            )
            if len(conn.entries) != 1:
                raise RuntimeError("Не удалось повторно прочитать учетную запись после блокировки")
            verified_uac = int(conn.entries[0].userAccountControl.value)
            if not verified_uac & 0x0002:
                raise RuntimeError("Повторное чтение не подтвердило флаг ACCOUNTDISABLE")

            return {
                "sam_account_name": str(entry.sAMAccountName.value),
                "user_dn": user_dn,
                "current_uac": current_uac,
                "new_uac": verified_uac,
                "moved_to_disabled_ou": moved,
            }

    async def execute(self, task: TaskDTO, policy: AutopilotPolicyDTO) -> ScenarioExecutionResult:
        """Autonomous execution of user account locking in Active Directory."""
        target_user = (task.entities.target_user or task.entities.user_name or "").strip()

        logger.info("Executing AccountLockScenario for ticket #%s (target_user: %s)", task.id, target_user)

        try:
            ad_res = await asyncio.to_thread(self._lock_ad_user_sync, target_user=target_user)

            sam = str(ad_res["sam_account_name"])
            user_dn = str(ad_res["user_dn"])
            current_uac = int(ad_res["current_uac"])
            new_uac = int(ad_res["new_uac"])

            resolution_comment = (
                f"Здравствуйте! Учетная запись сотрудника {target_user} ({sam}) успешно заблокирована в Active Directory. "
                "Доступ к корпоративным информационным системам прекращен."
            )

            technical_note = (
                f"🤖 [Автопилот: Блокировка учетной записи]\n"
                f"Учетная запись: {sam}\n"
                f"DN: {user_dn}\n"
                f"userAccountControl: {current_uac} ➔ {new_uac} (ACCOUNTDISABLE 0x0002 установлен)\n"
                f"Инициатор: {task.applicant_name or task.creator_name}\n"
                f"Статус: Выполнена (Status 3)"
            )

            return ScenarioExecutionResult(
                success=True,
                action_taken="account_lock",
                resolution_comment=resolution_comment,
                technical_note=technical_note,
                target_status_id=3,
                metadata={
                    "sam_account_name": sam,
                    "user_dn": user_dn,
                    "uac_before": current_uac,
                    "uac_after": new_uac,
                },
            )
        except Exception as exc:
            logger.error("Failed to lock AD account for ticket #%s: %s", task.id, exc, exc_info=True)
            return ScenarioExecutionResult(
                success=False,
                action_taken="account_lock",
                resolution_comment="",
                technical_note=(
                    f"⚠️ [Автопилот: Ошибка блокировки учетной записи в Active Directory]\n"
                    f"Целевой пользователь: {target_user}\n"
                    f"Ошибка: {exc}\n"
                    "Заявка передана на ручной разбор дежурному инженеру (Status 2)."
                ),
                target_status_id=2,
                error=str(exc),
            )
