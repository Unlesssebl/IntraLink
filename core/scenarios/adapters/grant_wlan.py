"""Corporate Wi-Fi (WLAN-WORKNET) access scenario."""

import asyncio
import logging
from typing import Optional

import ldap3
from ldap3.utils.conv import escape_filter_chars

from core.ad.pool import ActiveDirectoryPool
from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import TaskDTO
from core.intraservice.service_definition import ServiceDefinition
from core.scenarios.base import BaseScenario, PreconditionResult, ScenarioExecutionResult

logger = logging.getLogger("core.scenarios.adapters.grant_wlan")

WLAN_SERVICE_IDS = {63}
WLAN_GROUP = "WLAN-WORKNET-ALLOW"


class GrantWLANScenario(BaseScenario):
    """Autonomous scenario providing access to corporate WLAN-WORKNET network via AD group membership."""

    scenario_key = "grant_wlan"
    name = "Доступ к Wi-Fi WLAN-WORKNET"
    description = "Предоставление доступа к корпоративной сети Wi-Fi через группу безопасности Active Directory"
    semantic_prototypes = [
        "прошу предоставить доступ к корпоративному Wi-Fi WLAN-WORKNET",
        "нужно подключить ноутбук к беспроводной сети компании",
        "нет доступа к wifi в офисе, требуется добавить в группу",
        "хочу подключиться к вайфай, добавьте мою учетку",
        "беспроводная сеть не доступна для моего устройства",
    ]

    definition = ServiceDefinition(
        service_ids=[63],
        name="Доступ к Wi-Fi WLAN-WORKNET",
        required_facts=["target_user"],
        requires_online_host=False,
        clarification_template=(
            "Здравствуйте! Для предоставления доступа к корпоративной сети Wi-Fi (WLAN-WORKNET) "
            "укажите рабочий доменный логин или точное ФИО сотрудника."
        ),
        adapter_key="grant_wlan",
        min_confidence=0.80,
    )

    def __init__(self, ad_pool: Optional[ActiveDirectoryPool] = None) -> None:
        self.ad_pool = ad_pool or ActiveDirectoryPool()

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

    def _grant_wlan_sync(self, target_user: str) -> dict[str, str | bool]:
        domain = self.ad_pool.config.domain
        base_dn = ",".join(f"DC={part}" for part in domain.split(".") if part)
        escaped_user = escape_filter_chars(target_user)
        escaped_group = escape_filter_chars(WLAN_GROUP)

        with self.ad_pool.connection_scope(auto_bind=True) as conn:
            conn.search(
                search_base=base_dn,
                search_filter=(
                    f"(&(objectClass=user)(|(sAMAccountName={escaped_user})"
                    f"(userPrincipalName={escaped_user})(displayName={escaped_user})(cn={escaped_user})))"
                ),
                search_scope=ldap3.SUBTREE,
                attributes=["sAMAccountName", "distinguishedName"],
            )
            if len(conn.entries) == 0:
                raise RuntimeError(f"Пользователь '{target_user}' не найден в Active Directory")
            if len(conn.entries) != 1:
                raise RuntimeError(
                    f"Пользователь '{target_user}' найден неоднозначно: совпадений {len(conn.entries)}"
                )
            user_entry = conn.entries[0]
            user_dn = str(user_entry.distinguishedName.value)
            sam = str(user_entry.sAMAccountName.value)

            conn.search(
                search_base=base_dn,
                search_filter=f"(&(objectClass=group)(cn={escaped_group}))",
                search_scope=ldap3.SUBTREE,
                attributes=["distinguishedName", "member"],
            )
            if len(conn.entries) != 1:
                raise RuntimeError(
                    f"Группа '{WLAN_GROUP}' должна быть найдена ровно один раз; совпадений {len(conn.entries)}"
                )
            group_entry = conn.entries[0]
            group_dn = str(group_entry.distinguishedName.value)
            members = {str(value).casefold() for value in (group_entry.member.values or [])}
            already_member = user_dn.casefold() in members

            if not already_member:
                conn.modify(group_dn, {"member": [(ldap3.MODIFY_ADD, [user_dn])]})
                if conn.result.get("result") != 0 and conn.result.get("description") != "success":
                    raise RuntimeError(
                        f"LDAP не добавил пользователя в '{WLAN_GROUP}': {conn.result.get('description')}"
                    )

            conn.search(
                search_base=group_dn,
                search_filter="(objectClass=group)",
                search_scope=ldap3.BASE,
                attributes=["member"],
            )
            if len(conn.entries) != 1:
                raise RuntimeError("Не удалось повторно прочитать WLAN-группу после изменения")
            verified_members = {str(value).casefold() for value in (conn.entries[0].member.values or [])}
            if user_dn.casefold() not in verified_members:
                raise RuntimeError("Повторное чтение не подтвердило членство в WLAN-группе")

            return {
                "sam_account_name": sam,
                "user_dn": user_dn,
                "group_dn": group_dn,
                "already_member": already_member,
            }

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
            ad_res = await asyncio.to_thread(self._grant_wlan_sync, target_user)
            sam = str(ad_res["sam_account_name"])

            res_comment = (
                f"Здравствуйте! Учетной записи ({sam}) предоставлен доступ к "
                "защищенной корпоративной беспроводной сети **WLAN-WORKNET**.\n\n"
                "**Инструкция по подключению:**\n"
                "1. В списке доступных беспроводных сетей выберите **WLAN-WORKNET**.\n"
                "2. При запросе учетных данных введите ваш рабочий логин (без домена) и пароль Windows.\n"
                "3. При появлении запроса подтверждения сертификата нажмите **«Подключиться»**.\n\n"
                "Если у вас возникнут сложности с подключением на мобильном устройстве или ноутбуке, пожалуйста, обратитесь в Helpdesk."
            )

            tech_note = (
                f"🤖 [Автопилот: Выдача доступа к Wi-Fi]\n"
                f"Пользователь: {sam}\n"
                f"Целевая группа AD: {WLAN_GROUP}\n"
                f"Уже состоял в группе: {ad_res['already_member']}\n"
                "Членство подтверждено повторным чтением группы.\n"
                f"Заявка выполнена в режиме {policy.mode}."
            )

            return ScenarioExecutionResult(
                success=True,
                action_taken="grant_wlan_access",
                resolution_comment=res_comment,
                technical_note=tech_note,
                target_status_id=3,
                metadata={
                    "account": sam,
                    "group": WLAN_GROUP,
                    "already_member": ad_res["already_member"],
                    "membership_verified": True,
                },
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
                target_status_id=2,
                error=str(exc),
            )
