"""Verified LDAP capability for corporate WLAN access."""

from __future__ import annotations

import asyncio
from typing import Any

import ldap3
from ldap3.utils.conv import escape_filter_chars

from core.ad.pool import ActiveDirectoryPool
from core.automation.capabilities import (
    CapabilityExecution,
    CapabilityExecutionContext,
    CapabilityOutcome,
    CapabilityPreflight,
    PreflightStatus,
)

WLAN_GROUP = "WLAN-WORKNET-ALLOW"


class AddWlanGroupMemberExecutor:
    def __init__(self, ad_pool: ActiveDirectoryPool | None = None) -> None:
        self.ad_pool = ad_pool or ActiveDirectoryPool()

    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight:
        target_user = str(params.get("target_user", "")).strip()
        if not target_user:
            return CapabilityPreflight(status=PreflightStatus.failed, error_code="missing_target_user")
        try:
            identity = await asyncio.to_thread(self._resolve_identity_sync, target_user)
        except RuntimeError as exc:
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                checks=["unique_ad_user", "unique_wlan_group"],
                error_code=str(exc),
            )
        return CapabilityPreflight(
            status=PreflightStatus.passed,
            checks=["unique_ad_user", "unique_wlan_group"],
            details={"sam_account_name": identity["sam_account_name"]},
        )

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        target_user = str(params["target_user"]).strip()
        try:
            result = await asyncio.to_thread(self._add_member_sync, target_user)
        except Exception as exc:
            return CapabilityExecution(
                outcome=CapabilityOutcome.failed,
                error_code="ldap_membership_change_failed",
                error_message=str(exc),
            )
        return CapabilityExecution(
            outcome=CapabilityOutcome.succeeded,
            proof={
                "sam_account_name": result["sam_account_name"],
                "group": WLAN_GROUP,
                "already_member": result["already_member"],
                "membership_verified": True,
            },
        )

    def _base_dn(self) -> str:
        return ",".join(f"DC={part}" for part in self.ad_pool.config.domain.split(".") if part)

    def _resolve_identity_sync(self, target_user: str) -> dict[str, str]:
        escaped_user = escape_filter_chars(target_user)
        escaped_group = escape_filter_chars(WLAN_GROUP)
        with self.ad_pool.connection_scope(auto_bind=True) as connection:
            connection.search(
                search_base=self._base_dn(),
                search_filter=(
                    f"(&(objectClass=user)(|(sAMAccountName={escaped_user})"
                    f"(userPrincipalName={escaped_user})(displayName={escaped_user})(cn={escaped_user})))"
                ),
                search_scope=ldap3.SUBTREE,
                attributes=["sAMAccountName", "distinguishedName"],
            )
            if len(connection.entries) == 0:
                raise RuntimeError("ad_user_not_found")
            if len(connection.entries) != 1:
                raise RuntimeError("ad_user_ambiguous")
            user = connection.entries[0]
            connection.search(
                search_base=self._base_dn(),
                search_filter=f"(&(objectClass=group)(cn={escaped_group}))",
                search_scope=ldap3.SUBTREE,
                attributes=["distinguishedName", "member"],
            )
            if len(connection.entries) != 1:
                raise RuntimeError("wlan_group_not_unique")
            group = connection.entries[0]
            return {
                "sam_account_name": str(user.sAMAccountName.value),
                "user_dn": str(user.distinguishedName.value),
                "group_dn": str(group.distinguishedName.value),
            }

    def _add_member_sync(self, target_user: str) -> dict[str, str | bool]:
        identity = self._resolve_identity_sync(target_user)
        user_dn = identity["user_dn"]
        group_dn = identity["group_dn"]
        with self.ad_pool.connection_scope(auto_bind=True) as connection:
            connection.search(group_dn, "(objectClass=group)", ldap3.BASE, attributes=["member"])
            if len(connection.entries) != 1:
                raise RuntimeError("wlan_group_reread_failed")
            members = {str(value).casefold() for value in (connection.entries[0].member.values or [])}
            already_member = user_dn.casefold() in members
            if not already_member:
                connection.modify(group_dn, {"member": [(ldap3.MODIFY_ADD, [user_dn])]})
                if connection.result.get("result") != 0:
                    raise RuntimeError(f"ldap_modify_failed:{connection.result.get('description')}")
            connection.search(group_dn, "(objectClass=group)", ldap3.BASE, attributes=["member"])
            if len(connection.entries) != 1:
                raise RuntimeError("wlan_group_verify_read_failed")
            verified = {str(value).casefold() for value in (connection.entries[0].member.values or [])}
            if user_dn.casefold() not in verified:
                raise RuntimeError("wlan_membership_not_verified")
        return {**identity, "already_member": already_member}
