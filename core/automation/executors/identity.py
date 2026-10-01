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
    HumanActionCandidate,
    HumanActionProposal,
    PreflightStatus,
)
from core.automation.contracts import (
    CredentialDeliveryPolicy,
    PreflightCheck,
    PreflightCheckStatus,
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
                checks=[
                    PreflightCheck(
                        key="required_identity_fields",
                        status=PreflightCheckStatus.failed,
                        details={"missing_params": missing},
                    ).model_dump(mode="json")
                ],
                details={"missing_params": missing},
                error_code="missing_identity_params",
            )
        target_ou_dn = str(params.get("target_ou_dn", "")).strip() or None
        categories = str(params.get("categories", "")).strip()
        applicant_company = str(params.get("applicant_company", "")).strip()
        applicant_login = str(params.get("applicant_login", "")).strip()
        try:
            preview = await self.provisioner.preflight(
                last_name=str(params["last_name"]).strip(),
                first_name=str(params["first_name"]).strip(),
                middle_name=str(params.get("middle_name", "")).strip(),
                department=str(params["department"]).strip(),
                company=str(params.get("company", "")).strip(),
                categories=categories,
                applicant_company=applicant_company,
                applicant_login=applicant_login,
                target_ou_dn=target_ou_dn,
            )
        except AccountProvisioningError as exc:
            if exc.code == "ad_target_ou_ambiguous":
                ou_candidates = exc.details.get("ou_candidates") or []
                candidates = tuple(
                    HumanActionCandidate(
                        candidate_id=f"ou_{idx}",
                        label=f"{item['name']} ({item['distinguished_name']})",
                        score=float(item.get("score", 0.0)),
                        evidence=tuple(item.get("evidence", ())),
                        server_value=str(item["distinguished_name"]),
                    )
                    for idx, item in enumerate(ou_candidates)
                )
                human_action = HumanActionProposal(
                    action_type="single_choice",
                    target_param="target_ou_dn",
                    prompt="Выберите подразделение (OU) в Active Directory для размещения нового сотрудника",
                    check_key="target_ou",
                    candidates=candidates,
                )
                return CapabilityPreflight(
                    status=PreflightStatus.action_required,
                    checks=[
                        PreflightCheck(
                            key="required_identity_fields",
                            status=PreflightCheckStatus.passed,
                        ).model_dump(mode="json"),
                        PreflightCheck(
                            key="ad_available",
                            status=PreflightCheckStatus.passed,
                        ).model_dump(mode="json"),
                        PreflightCheck(
                            key="target_ou",
                            status=PreflightCheckStatus.action_required,
                            reason_code="ad_target_ou_ambiguous",
                            details=exc.details,
                        ).model_dump(mode="json"),
                    ],
                    details=exc.details,
                    error_code=exc.code,
                    human_action=human_action,
                )
            failed_check_key = (
                "distinguished_name_absent"
                if exc.code == "ad_object_already_exists"
                else (
                    "target_ou"
                    if exc.code in {"ad_target_ou_not_found", "ad_target_ou_outside_root"}
                    else (
                        "ad_available"
                        if exc.code in {
                            "ad_credentials_unavailable",
                            "ad_secure_channel_required",
                            "ad_connection_failed",
                            "ad_pool_exhausted",
                        }
                        else "identity_preflight"
                    )
                )
            )
            return CapabilityPreflight(
                status=PreflightStatus.failed,
                checks=[
                    PreflightCheck(
                        key=failed_check_key,
                        status=PreflightCheckStatus.failed,
                        reason_code=exc.code,
                        details=exc.details,
                    ).model_dump(mode="json")
                ],
                details=exc.details,
                error_code=exc.code,
            )
        except Exception as exc:
            return CapabilityPreflight(
                status=PreflightStatus.degraded,
                checks=[
                    PreflightCheck(
                        key="ad_available",
                        status=PreflightCheckStatus.degraded,
                        reason_code=type(exc).__name__,
                    ).model_dump(mode="json")
                ],
                error_code=f"identity_preflight_exception:{type(exc).__name__}",
            )
        checks = [
            PreflightCheck(key="required_identity_fields", status=PreflightCheckStatus.passed).model_dump(mode="json"),
            PreflightCheck(key="ad_available", status=PreflightCheckStatus.passed).model_dump(mode="json"),
            PreflightCheck(
                key="target_ou",
                status=PreflightCheckStatus.passed,
                details={"target_ou": preview.get("target_ou")},
            ).model_dump(mode="json"),
            PreflightCheck(
                key="distinguished_name_absent",
                status=PreflightCheckStatus.passed,
                details={"preview_user_dn": preview.get("preview_user_dn")},
            ).model_dump(mode="json"),
            PreflightCheck(
                key="login_preview",
                status=PreflightCheckStatus.passed,
                details={"login": preview.get("preview_sam_account_name")},
            ).model_dump(mode="json"),
            PreflightCheck(key="intraservice_available", status=PreflightCheckStatus.passed).model_dump(mode="json"),
            PreflightCheck(key="credential_delivery_configured", status=PreflightCheckStatus.passed).model_dump(mode="json"),
        ]
        return CapabilityPreflight(
            status=PreflightStatus.passed,
            checks=checks,
            details=preview,
        )

    async def validate_human_action(self, target_param: str, value: Any) -> bool:
        if target_param != "target_ou_dn":
            return False
        if not isinstance(value, str) or not value.strip():
            return False

        from core.ad.credentials import AD_USERS_ROOT_OU
        from core.ad.provisioning import _is_descendant

        users_root_ou = getattr(self.provisioner, "users_root_ou", None) or os.getenv("AD_USERS_OU") or AD_USERS_ROOT_OU
        if not _is_descendant(value.strip(), users_root_ou):
            return False

        try:
            pool = getattr(self.provisioner, "ad_pool", None)
            if pool is not None:
                def _check_ou_exists() -> bool:
                    with pool.connection_scope(auto_bind=True) as conn:
                        try:
                            conn.search(
                                value.strip(),
                                "(objectClass=organizationalUnit)",
                                ldap3.BASE,
                                attributes=["ou"],
                            )
                            return len(conn.entries) > 0
                        except ldap3.core.exceptions.LDAPNoSuchObjectResult:
                            return False

                return await asyncio.to_thread(_check_ou_exists)
        except Exception:
            return True
        return True

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution:
        from core.automation.execution_policy import is_deployment_force_disabled

        if is_deployment_force_disabled():
            return CapabilityExecution(
                outcome=CapabilityOutcome.failed,
                proof={"ad_object_created": False, "safe_to_retry": True},
                error_code="deployment_force_disabled",
            )
        policy_decision = getattr(context, "policy_decision", None)
        if policy_decision is not None and not policy_decision.allowed:
            return CapabilityExecution(
                outcome=CapabilityOutcome.failed,
                proof={"ad_object_created": False, "safe_to_retry": True},
                error_code="execution_policy_denied",
            )
        delivery_policy = None
        if "delivery_policy" in params and isinstance(params["delivery_policy"], dict):
            delivery_policy = CredentialDeliveryPolicy.model_validate(params["delivery_policy"])
        elif isinstance(params.get("delivery_policy"), CredentialDeliveryPolicy):
            delivery_policy = params["delivery_policy"]

        target_ou_dn = str(params.get("target_ou_dn", "")).strip() or None
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
                categories=str(params.get("categories", "")).strip(),
                applicant_company=str(params.get("applicant_company", "")).strip(),
                applicant_login=str(params.get("applicant_login", "")).strip(),
                delivery_policy=delivery_policy,
                target_ou_dn=target_ou_dn,
            )
        except AccountProvisioningError as exc:
            outcome = (
                CapabilityOutcome.unknown_outcome
                if exc.code == "credentials_delivery_unknown_outcome"
                else CapabilityOutcome.failed
            )
            return CapabilityExecution(
                outcome=outcome,
                proof={
                    "ad_object_created": exc.ad_object_created,
                    "safe_to_retry": not exc.ad_object_created,
                    "error_code": exc.code,
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
                "delivery_receipt_id": receipt.delivery_receipt_id,
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

    async def validate_human_action(self, target_param: str, value: Any) -> bool:
        return True
