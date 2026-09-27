"""Verified Active Directory identity capabilities."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import ldap3
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from core.ad.pool import ActiveDirectoryPool
from core.ad.provisioning import AccountProvisioningError, AccountProvisioningService
from core.automation.capabilities import (
    CapabilityExecution,
    CapabilityExecutionContext,
    CapabilityOutcome,
    CapabilityPreflight,
    PreflightStatus,
)


class CreateAdUserExecutor:
    def __init__(self, provisioner: AccountProvisioningService | None = None) -> None:
        self.provisioner = provisioner or AccountProvisioningService()

    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        required = ("first_name", "last_name", "department", "title")
        missing = [key for key in required if not str(params.get(key, "")).strip()]
        if missing:
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                details={"missing_params": missing},
                error_code="missing_identity_params",
            )
        return CapabilityPreflight(
            status=PreflightStatus.passed,
            checks=["required_identity_fields", "credentials_delivery_configured"],
        )

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        try:
            receipt = await self.provisioner.provision(
                task_id=context.task_id,
                last_name=str(params["last_name"]).strip(),
                first_name=str(params["first_name"]).strip(),
                middle_name=str(params.get("middle_name", "")).strip(),
                department=str(params["department"]).strip(),
                title=str(params["title"]).strip(),
                phone=str(params.get("phone", "")).strip(),
                company=str(params.get("company", "")).strip(),
            )
        except AccountProvisioningError as exc:
            return CapabilityExecution(
                outcome=CapabilityOutcome.failed,
                proof={
                    "ad_object_created": exc.ad_object_created,
                    "safe_to_retry": False,
                },
                error_code=exc.code,
                error_message=str(exc),
            )
        except Exception as exc:
            return CapabilityExecution(
                outcome=CapabilityOutcome.failed,
                proof={"ad_object_created": False, "safe_to_retry": False},
                error_code="provisioning_unexpected_error",
                error_message=str(exc),
            )
        return CapabilityExecution(
            outcome=CapabilityOutcome.succeeded,
            proof={
                "sam_account_name": receipt.sam_account_name,
                "upn": receipt.upn,
                "user_dn": receipt.user_dn,
                "credentials_written": receipt.credentials_written,
            },
        )


class DisableAdUserExecutor:
    def __init__(self, ad_pool: ActiveDirectoryPool | None = None) -> None:
        self.ad_pool = ad_pool or ActiveDirectoryPool()

    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        target_user = str(params.get("target_user", "")).strip()
        if not target_user:
            return CapabilityPreflight(status=PreflightStatus.failed, error_code="missing_target_user")
        try:
            identity = await asyncio.to_thread(self._find_unique_sync, target_user)
        except RuntimeError as exc:
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                checks=["unique_ad_user"],
                error_code=str(exc),
            )
        return CapabilityPreflight(
            status=PreflightStatus.passed,
            checks=["unique_ad_user"],
            details={"sam_account_name": identity["sam_account_name"]},
        )

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        target_user = str(params["target_user"]).strip()
        try:
            result = await asyncio.to_thread(self._disable_sync, target_user)
        except Exception as exc:
            return CapabilityExecution(
                outcome=CapabilityOutcome.failed,
                error_code="ad_disable_failed",
                error_message=str(exc),
            )
        return CapabilityExecution(
            outcome=CapabilityOutcome.succeeded,
            proof={
                "sam_account_name": result["sam_account_name"],
                "user_dn": result["user_dn"],
                "uac_before": result["uac_before"],
                "uac_after": result["uac_after"],
                "account_disabled_verified": True,
                "moved_to_disabled_ou": result["moved_to_disabled_ou"],
            },
        )

    def _base_dn(self) -> str:
        return ",".join(f"DC={part}" for part in self.ad_pool.config.domain.split(".") if part)

    def _find_unique_sync(self, target_user: str) -> dict[str, str | int]:
        escaped = escape_filter_chars(target_user)
        with self.ad_pool.connection_scope(auto_bind=True) as connection:
            connection.search(
                self._base_dn(),
                (
                    f"(&(objectClass=user)(|(sAMAccountName={escaped})"
                    f"(userPrincipalName={escaped})(displayName={escaped})(cn={escaped})))"
                ),
                ldap3.SUBTREE,
                attributes=["sAMAccountName", "distinguishedName", "userAccountControl", "cn"],
            )
            if len(connection.entries) == 0:
                raise RuntimeError("ad_user_not_found")
            if len(connection.entries) != 1:
                raise RuntimeError("ad_user_ambiguous")
            entry = connection.entries[0]
            return {
                "sam_account_name": str(entry.sAMAccountName.value),
                "user_dn": str(entry.distinguishedName.value),
                "uac": int(entry.userAccountControl.value),
                "cn": str(entry.cn.value or entry.sAMAccountName.value),
            }

    def _disable_sync(self, target_user: str) -> dict[str, str | int | bool]:
        identity = self._find_unique_sync(target_user)
        user_dn = str(identity["user_dn"])
        uac_before = int(identity["uac"])
        uac_after = uac_before | 0x0002
        moved = False
        with self.ad_pool.connection_scope(auto_bind=True) as connection:
            connection.modify(user_dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [uac_after])]})
            if connection.result.get("result") != 0:
                raise RuntimeError(f"ldap_disable_failed:{connection.result.get('description')}")
            disabled_ou = os.getenv("AD_DISABLED_OU")
            if disabled_ou:
                rdn = f"CN={escape_rdn(str(identity['cn']))}"
                connection.modify_dn(user_dn, rdn, new_superior=disabled_ou)
                if connection.result.get("result") != 0:
                    raise RuntimeError(f"ldap_move_failed:{connection.result.get('description')}")
                moved = True
                user_dn = f"{rdn},{disabled_ou}"
            connection.search(
                user_dn,
                "(objectClass=user)",
                ldap3.BASE,
                attributes=["sAMAccountName", "distinguishedName", "userAccountControl"],
            )
            if len(connection.entries) != 1:
                raise RuntimeError("ad_user_verify_read_failed")
            verified_uac = int(connection.entries[0].userAccountControl.value)
            if not verified_uac & 0x0002:
                raise RuntimeError("ad_user_disable_not_verified")
        return {
            "sam_account_name": identity["sam_account_name"],
            "user_dn": user_dn,
            "uac_before": uac_before,
            "uac_after": verified_uac,
            "moved_to_disabled_ou": moved,
        }
