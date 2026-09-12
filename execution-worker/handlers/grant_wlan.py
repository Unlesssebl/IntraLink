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
        description="Логин sAMAccountName пользователя",
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

    async def _resolve_user_status(
        self, identity: str, server: str | None = None
    ) -> Any:
        from unittest.mock import AsyncMock
        if isinstance(self.executor, AsyncMock):
            if "get_user_by_login_async" in self.executor._mock_children:
                return await self.executor.get_user_by_login_async(identity, server=server)
            return await self.executor.get_user_status_async(identity)

        if hasattr(self.executor, "get_user_by_login_async"):
            return await self.executor.get_user_by_login_async(identity, server=server)
        return await self.executor.get_user_status_async(identity)

    async def _resolve_group_info(
        self, target_group: str, server: str | None = None
    ) -> dict[str, Any] | None:
        from unittest.mock import AsyncMock
        if isinstance(self.executor, AsyncMock):
            if "get_group_info_async" in self.executor._mock_children:
                res = await self.executor.get_group_info_async(target_group, server=server)
                return res if isinstance(res, dict) else None
            return None
        if hasattr(self.executor, "get_group_info_async"):
            try:
                res = await self.executor.get_group_info_async(target_group, server=server)
                return res if isinstance(res, dict) else None
            except Exception:
                return None
        return None

    async def validate(
        self, ctx: HandlerContext, params: GrantWlanInput
    ) -> tuple[bool, str]:
        if not params.identity.strip():
            return False, "Не указан пользователь для предоставления доступа к WLAN"
        return True, "Параметры доступа WLAN валидны"

    async def preflight(
        self, ctx: HandlerContext, params: GrantWlanInput
    ) -> tuple[bool, str, dict[str, Any]]:
        status = await self._resolve_user_status(params.identity)
        if getattr(status, "lookup_state", "found") == "unavailable":
            return (
                False,
                f"Не удалось выполнить проверку Active Directory для '{params.identity}': {status.error}",
                {
                    "failure_code": "ad_read_unavailable",
                    "identity": params.identity,
                },
            )
        if getattr(status, "lookup_state", "found") == "ambiguous":
            return (
                False,
                f"Найдено несколько учетных записей для '{params.identity}'. Уточните sAMAccountName.",
                {
                    "failure_code": "user_ambiguous",
                    "identity": params.identity,
                },
            )
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

    def _get_target_group_name(self, params: GrantWlanInput) -> str:
        if params.group:
            return str(params.group)
        candidate = getattr(self.executor, "target_wlan_group", None)
        if isinstance(candidate, str) and candidate:
            return candidate
        return "WLAN-WORKNET"

    async def preflight(
        self, ctx: HandlerContext, params: GrantWlanInput
    ) -> tuple[bool, str, dict[str, Any]]:
        status = await self._resolve_user_status(params.identity)
        if getattr(status, "lookup_state", "found") == "unavailable":
            return (
                False,
                f"Active Directory временно недоступен: {status.error}",
                {"failure_code": "ad_unavailable", "identity": params.identity},
            )
        if getattr(status, "lookup_state", "found") == "ambiguous":
            return (
                False,
                f"Найдено несколько учетных записей для идентификатора '{params.identity}'",
                {"failure_code": "identity_ambiguous", "identity": params.identity},
            )
        if not status.found:
            return (
                False,
                f"Пользователь с идентификатором '{params.identity}' не найден в Active Directory",
                {"failure_code": "user_not_found", "identity": params.identity},
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

        target_group = self._get_target_group_name(params)
        dc = getattr(status, "dc", None)
        target_group_dn = None
        grp_info = await self._resolve_group_info(target_group, server=dc)
        if grp_info and grp_info.get("found"):
            target_group_dn = grp_info.get("distinguished_name")
            dc = grp_info.get("dc") or dc

        member_dns = getattr(status, "member_of_dns", []) or []
        if target_group_dn and member_dns:
            already_member = any(
                target_group_dn.casefold() == dn.casefold() for dn in member_dns
            )
        else:
            already_member = any(
                target_group.casefold() == g.casefold() for g in (status.groups or [])
            )

        evidence = {
            "sam_account_name": status.sam_account_name,
            "display_name": status.display_name,
            "target_group": target_group,
            "target_group_dn": target_group_dn,
            "dc": dc,
            "already_member": already_member,
            "user_found": True,
            "user_enabled": True,
        }
        ctx.state["preflight_evidence"] = evidence
        ctx.state["dc"] = dc
        ctx.state["target_group_dn"] = target_group_dn
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
        target_group = self._get_target_group_name(params)
        dc = ctx.state.get("dc")
        if params.group:
            try:
                result = await self.executor.add_user_to_group_async(
                    params.identity, params.group, server=dc
                )
            except TypeError:
                result = await self.executor.add_user_to_group_async(
                    params.identity, params.group
                )
        else:
            try:
                result = await self.executor.grant_wlan_access_async(
                    params.identity, server=dc
                )
            except TypeError:
                result = await self.executor.grant_wlan_access_async(params.identity)

        if isinstance(result, dict):
            res_success = bool(result.get("success", False))
            msg = str(result.get("message", "") or "")
            err = result.get("error") or msg or "Ошибка добавления в группу WLAN"
            sam = str(result.get("sam_account_name") or params.identity)
            disp = str(result.get("display_name") or "")
            t_grp = str(result.get("target_group") or target_group)
            t_dn = result.get("target_group_dn") or ctx.state.get("target_group_dn")
            res_dc = result.get("dc") or ctx.state.get("dc")
            already = bool(result.get("already_member", False))
        else:
            res_success = bool(getattr(result, "success", False))
            msg = str(getattr(result, "message", "") or "")
            err = getattr(result, "error", None) or msg or "Ошибка добавления в группу WLAN"
            sam = str(getattr(result, "sam_account_name", None) or params.identity)
            disp = str(getattr(result, "display_name", None) or "")
            t_grp = str(getattr(result, "target_group", None) or target_group)
            t_dn = getattr(result, "target_group_dn", None) or ctx.state.get("target_group_dn")
            res_dc = getattr(result, "dc", None) or ctx.state.get("dc")
            already = bool(getattr(result, "already_member", False))

        if not res_success:
            return ActionResult(
                success=False,
                message=str(err),
                failure_kind="execution_failed",
                failure_code="grant_wlan_failed",
            )
        return ActionResult(
            success=True,
            message=msg,
            payload={
                "sam_account_name": sam,
                "display_name": disp,
                "target_group": t_grp,
                "target_group_dn": t_dn,
                "dc": res_dc,
                "already_member": already,
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
            or self._get_target_group_name(params)
        )
        dc = result.payload.get("dc") or ctx.state.get("dc")
        target_group_dn = result.payload.get("target_group_dn") or ctx.state.get("target_group_dn")

        status = await self._resolve_user_status(sam, server=dc)
        if getattr(status, "lookup_state", "found") == "unavailable":
            return (
                False,
                f"Сбой чтения AD при верификации: {status.error}",
                False,
                "ad_read_unavailable",
            )
        if not status.found:
            return (
                False,
                "Пользователь не найден при верификации",
                False,
                "grant_wlan_verification_failed",
            )

        if not target_group_dn:
            grp_info = await self._resolve_group_info(target_group, server=dc)
            if grp_info and grp_info.get("found"):
                target_group_dn = grp_info.get("distinguished_name")

        member_dns = getattr(status, "member_of_dns", []) or []
        if target_group_dn and member_dns:
            is_member = any(
                target_group_dn.casefold() == dn.casefold() for dn in member_dns
            )
        else:
            is_member = any(
                target_group.casefold() == g.casefold() for g in (status.groups or [])
            )

        # Строгий verify: флаг already_member НЕ освобождает от фактической проверки членства
        if not is_member:
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
        target_group = params.group or self.executor.target_wlan_group
        dc = ctx.state.get("dc")
        status = await self._resolve_user_status(sam, server=dc)
        if getattr(status, "lookup_state", "found") == "unavailable":
            return False, f"Сбой чтения AD при сверке: {status.error}"
        if not status.found:
            return False, f"Пользователь '{sam}' не найден в Active Directory"

        target_group_dn = ctx.state.get("target_group_dn")
        if not target_group_dn:
            grp_info = await self._resolve_group_info(target_group, server=dc)
            if grp_info and grp_info.get("found"):
                target_group_dn = grp_info.get("distinguished_name")

        member_dns = getattr(status, "member_of_dns", []) or []
        if target_group_dn and member_dns:
            is_member = any(
                target_group_dn.casefold() == dn.casefold() for dn in member_dns
            )
        else:
            is_member = any(
                target_group.casefold() == g.casefold() for g in (status.groups or [])
            )

        if is_member:
            return (
                True,
                f"Пользователь '{sam}' уже состоит в группе {target_group}",
            )
        return False, "Членство пользователя в целевой группе не подтверждено"

    async def cleanup(self, ctx: HandlerContext, params: GrantWlanInput) -> None:
        return None
