"""Fail-closed v2 handler for Active Directory WLAN access (WLAN-WORKNET)."""

from __future__ import annotations

import re
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator

from executors.ad import ActiveDirectoryExecutor
from sdk.base import ActionHandler
from sdk.models import ActionResult, HandlerContext, RiskClass


class GrantWlanInput(BaseModel):
    """Входные данные для предоставления доступа к Wi-Fi."""

    identity: str = Field(
        ...,
        min_length=2,
        max_length=128,
        description="Логин, UPN или ФИО пользователя",
    )
    group: str | None = Field(
        default=None,
        max_length=64,
        description="Целевая группа доступа (по умолчанию WLAN-WORKNET)",
    )
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Identity не может быть пустым")
        if re.search(r"[;|<>&`$\x00\r\n]", clean):
            raise ValueError(f"Недопустимые спецсимволы в identity: {v}")
        return clean


class GrantWlanHandler(ActionHandler[GrantWlanInput]):
    """Обработчик выдачи доступа к Wi-Fi сети (WLAN-WORKNET) с 7-фазным контрактом."""

    id = "grant_wlan"
    version = "2.0.0"
    risk_class = RiskClass.NEVER_AUTO_RETRY
    capabilities = ["windows", "ad"]
    input_model = GrantWlanInput

    def __init__(self, executor: ActiveDirectoryExecutor | None = None):
        self.executor = executor or ActiveDirectoryExecutor()

    async def validate(
        self, ctx: HandlerContext, params: GrantWlanInput
    ) -> tuple[bool, str]:
        if not params.identity.strip():
            return False, "Не указан пользователь для предоставления доступа к WLAN"
        return True, "Параметры доступа WLAN валидны"

    async def preflight(
        self, ctx: HandlerContext, params: GrantWlanInput
    ) -> tuple[bool, str, dict[str, Any]]:
        status = await self.executor.get_user_status_async(params.identity)
        if not status.found:
            return (
                False,
                f"Пользователь '{params.identity}' не найден в Active Directory",
                {
                    "failure_code": "user_not_found",
                    "identity": params.identity,
                },
            )
        if not status.enabled:
            return (
                False,
                f"Учётная запись '{status.sam_account_name}' отключена в Active Directory",
                {
                    "failure_code": "user_account_disabled",
                    "sam_account_name": status.sam_account_name,
                },
            )

        target_group = params.group or self.executor.target_wlan_group
        already_member = any(
            target_group.lower() in g.lower() for g in (status.groups or [])
        )
        evidence = {
            "sam_account_name": status.sam_account_name,
            "display_name": status.display_name,
            "target_group": target_group,
            "already_member": already_member,
            "user_found": True,
            "user_enabled": True,
        }
        ctx.state["preflight_evidence"] = evidence
        if ctx.core_api_client and ctx.claim_token:
            await ctx.core_api_client.record_command_preflight_v2(
                ctx.command_id,
                ctx.node_id,
                ctx.claim_token,
                evidence,
            )
        return True, "AD preflight доступа WLAN завершён", evidence

    async def execute(
        self, ctx: HandlerContext, params: GrantWlanInput
    ) -> ActionResult:
        target_group = params.group or self.executor.target_wlan_group
        if params.group:
            result = await self.executor.add_user_to_group_async(
                params.identity, params.group
            )
        else:
            result = await self.executor.grant_wlan_access_async(params.identity)

        if not result.success:
            return ActionResult(
                success=False,
                message=result.error or result.message or "Ошибка добавления в группу WLAN",
                failure_kind="execution_failed",
                failure_code="grant_wlan_failed",
            )
        return ActionResult(
            success=True,
            message=result.message,
            payload={
                "sam_account_name": result.sam_account_name,
                "display_name": result.display_name,
                "target_group": result.target_group,
                "already_member": result.already_member,
            },
        )

    async def verify(
        self,
        ctx: HandlerContext,
        params: GrantWlanInput,
        result: ActionResult,
    ) -> tuple[bool, str, bool, str | None]:
        if not result.success:
            return False, result.message, True, result.failure_code
        sam = str(result.payload.get("sam_account_name") or params.identity)
        target_group = str(
            result.payload.get("target_group")
            or params.group
            or self.executor.target_wlan_group
        )
        status = await self.executor.get_user_status_async(sam)
        if not status.found:
            return (
                False,
                "Пользователь не найден при верификации",
                False,
                "grant_wlan_verification_failed",
            )
        is_member = any(
            target_group.lower() in g.lower() for g in (status.groups or [])
        )
        if not is_member and not result.payload.get("already_member"):
            return (
                False,
                f"Пользователь '{sam}' не обнаружен в группе {target_group} после добавления",
                False,
                "grant_wlan_verification_failed",
            )
        result.payload["verified"] = True
        return (
            True,
            f"Пользователь '{sam}' верифицирован в группе {target_group}",
            False,
            None,
        )

    async def reconcile(
        self, ctx: HandlerContext, params: GrantWlanInput
    ) -> tuple[bool, str]:
        sam = params.identity
        status = await self.executor.get_user_status_async(sam)
        target_group = params.group or self.executor.target_wlan_group
        if status.found and any(
            target_group.lower() in g.lower() for g in (status.groups or [])
        ):
            return (
                True,
                f"Пользователь '{sam}' уже состоит в группе {target_group}",
            )
        return False, "Членство пользователя в целевой группе не подтверждено"

    async def cleanup(self, ctx: HandlerContext, params: GrantWlanInput) -> None:
        return None
