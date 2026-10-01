"""Account provisioning boundary that never returns or logs plaintext credentials."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

import ldap3
from ldap3.core.exceptions import LDAPNoSuchObjectResult
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from core.ad.credentials import AD_USERS_ROOT_OU, ActiveDirectoryCredentialVault
from core.ad.group_resolver import (
    AD_SERVICE_DESK_GROUPS_OU,
    GROUPS_CACHE_KEY_PREFIX,
    GROUPS_CACHE_TTL_SECONDS,
    ActiveDirectoryGroup,
    ActiveDirectoryGroupResolver,
)
from core.ad.ou_resolver import OrganizationalUnit, OrganizationalUnitResolver, _is_descendant
from core.ad.password import generate_secure_password
from core.ad.pool import ActiveDirectoryPool, ADPoolConfig
from core.ad.transliteration import generate_sam_account_name
from core.automation.contracts import (
    CredentialDeliveryField,
    CredentialDeliveryPolicy,
    DeliverySensitivity,
)
from core.intraservice.auth import ServiceAuthBootstrap
from core.intraservice.client import IntraServiceClient

logger = logging.getLogger(__name__)

OU_CACHE_KEY_PREFIX = "ad:ou_tree"
OU_CACHE_TTL_SECONDS = 3600

DEFAULT_AD_DELIVERY_POLICY = CredentialDeliveryPolicy(
    channel="intraservice_custom_fields",
    fields=(
        CredentialDeliveryField(
            key="user_login",
            field_id=1439,
            sensitivity=DeliverySensitivity.public,
            required=True,
        ),
        CredentialDeliveryField(
            key="initial_password",
            field_id=1440,
            sensitivity=DeliverySensitivity.secret,
            required=True,
        ),
    ),
    verify_after_write=True,
)


class CredentialDeliveryOutcome(str, Enum):
    succeeded = "succeeded"
    definitely_failed = "definitely_failed"
    unknown_outcome = "unknown_outcome"


@dataclass(frozen=True)
class CredentialDeliveryResult:
    outcome: CredentialDeliveryOutcome
    receipt_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    written_fields: tuple[int, ...] = ()


class CredentialDeliveryPort(Protocol):
    async def deliver(
        self,
        *,
        task_id: int,
        policy: CredentialDeliveryPolicy,
        public_values: dict[str, str],
        secret_values: dict[str, str],
    ) -> CredentialDeliveryResult: ...


class IntraServiceCredentialDeliveryPort:
    def __init__(
        self,
        client: IntraServiceClient | None = None,
        auth_bootstrap: ServiceAuthBootstrap | None = None,
    ) -> None:
        self.client = client or IntraServiceClient()
        self.auth_bootstrap = auth_bootstrap or ServiceAuthBootstrap()

    async def deliver(
        self,
        *,
        task_id: int,
        policy: CredentialDeliveryPolicy,
        public_values: dict[str, str],
        secret_values: dict[str, str],
    ) -> CredentialDeliveryResult:
        custom_fields: dict[int, str] = {}
        for field in policy.fields:
            if field.key in public_values:
                custom_fields[field.field_id] = public_values[field.key]
            elif field.key in secret_values:
                custom_fields[field.field_id] = secret_values[field.key]
            elif field.required:
                return CredentialDeliveryResult(
                    outcome=CredentialDeliveryOutcome.definitely_failed,
                    error_code="missing_required_delivery_value",
                    error_message=f"Missing delivery value for required field '{field.key}'",
                )

        try:
            credentials = await self.auth_bootstrap.bootstrap_auth(client=self.client)
            await self.client.update_task(
                task_id=task_id,
                custom_fields=custom_fields,
                auth_b64=credentials.auth_b64,
            )
        except Exception as exc:
            err_str = str(exc).lower()
            if any(term in err_str for term in ("400", "401", "403", "404", "bad request", "unauthorized")):
                return CredentialDeliveryResult(
                    outcome=CredentialDeliveryOutcome.definitely_failed,
                    error_code="delivery_rejected_by_server",
                    error_message=str(exc),
                )
            return CredentialDeliveryResult(
                outcome=CredentialDeliveryOutcome.unknown_outcome,
                error_code="delivery_network_or_server_error",
                error_message=str(exc),
            )

        if policy.verify_after_write:
            try:
                task = await self.client.get_task(task_id=task_id, auth_b64=credentials.auth_b64)
                task_fields = {str(k): str(v) for k, v in (task.custom_fields or {}).items()}
                for field in policy.fields:
                    if field.sensitivity == DeliverySensitivity.public and field.key in public_values:
                        expected = public_values[field.key]
                        actual = task_fields.get(str(field.field_id)) or task_fields.get(f"Поле_{field.field_id}")
                        if actual is None and task.custom_field_data:
                            match = re.search(
                                rf"""<field id=['"]{field.field_id}['"]>([^<]*)</field>""",
                                task.custom_field_data,
                            )
                            if match:
                                actual = match.group(1).strip()
                        actual = actual or ""
                        if actual.strip().casefold() != expected.strip().casefold():
                            return CredentialDeliveryResult(
                                outcome=CredentialDeliveryOutcome.unknown_outcome,
                                error_code="delivery_verification_failed",
                                error_message=f"Read-back of field {field.field_id} did not match expected value",
                                written_fields=tuple(custom_fields.keys()),
                            )
            except Exception as exc:
                return CredentialDeliveryResult(
                    outcome=CredentialDeliveryOutcome.unknown_outcome,
                    error_code="delivery_verify_read_error",
                    error_message=str(exc),
                    written_fields=tuple(custom_fields.keys()),
                )

        receipt_id = f"del_{uuid.uuid4().hex[:12]}"
        return CredentialDeliveryResult(
            outcome=CredentialDeliveryOutcome.succeeded,
            receipt_id=receipt_id,
            written_fields=tuple(custom_fields.keys()),
        )


@dataclass(frozen=True)
class AccountProvisioningReceipt:
    sam_account_name: str
    upn: str
    user_dn: str
    full_name: str
    credentials_written: bool = True
    delivery_receipt_id: str | None = None
    service_group: str | None = None
    service_group_added: bool = False
    service_group_error: str | None = None


class AccountProvisioningError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        ad_object_created: bool = False,
        retryable: bool = False,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.ad_object_created = ad_object_created
        self.retryable = retryable
        self.details = details or {}


class AccountProvisioningService:
    """Create and verify an AD user, then atomically deliver credentials to ticket fields."""

    def __init__(
        self,
        ad_pool: ActiveDirectoryPool | None = None,
        client: IntraServiceClient | None = None,
        auth_bootstrap: ServiceAuthBootstrap | None = None,
        credential_vault: ActiveDirectoryCredentialVault | None = None,
        delivery_port: CredentialDeliveryPort | None = None,
        group_resolver: ActiveDirectoryGroupResolver | None = None,
    ) -> None:
        self.ad_pool = ad_pool
        self.client = client or IntraServiceClient()
        self.auth_bootstrap = auth_bootstrap or ServiceAuthBootstrap()
        self.credential_vault = credential_vault or ActiveDirectoryCredentialVault()
        self.delivery_port = delivery_port
        self.ou_resolver = OrganizationalUnitResolver()
        self.group_resolver = group_resolver or ActiveDirectoryGroupResolver()

    async def _connection_boundary(self) -> tuple[ActiveDirectoryPool, str]:
        if self.ad_pool is not None:
            return self.ad_pool, os.getenv("AD_USERS_OU") or AD_USERS_ROOT_OU
        try:
            settings = await self.credential_vault.load()
        except Exception as exc:
            raise AccountProvisioningError(
                "ad_credentials_unavailable",
                "Active Directory credentials are not configured",
            ) from exc
        if not settings.use_ssl:
            raise AccountProvisioningError(
                "ad_secure_channel_required",
                "LDAPS is required for Active Directory password provisioning",
            )
        pool = ActiveDirectoryPool(
            ADPoolConfig(
                servers=list(settings.servers),
                connect_timeout=settings.connect_timeout,
                port=settings.port,
                use_ssl=settings.use_ssl,
                bind_user=settings.username,
                bind_password=settings.password,
                domain=settings.domain,
            )
        )
        return pool, settings.users_root_ou

    async def _get_cached_ou_tree(self, domain: str, users_root_ou: str) -> list[OrganizationalUnit] | None:
        try:
            from core.redis_client import get_redis_client

            redis = get_redis_client()
            key = f"{OU_CACHE_KEY_PREFIX}:{domain.lower()}:{users_root_ou.strip().lower()}"
            raw = await redis.get(key)
            if not raw:
                return None
            data = json.loads(raw)
            if isinstance(data, list):
                return [
                    OrganizationalUnit(name=item["name"], distinguished_name=item["distinguished_name"])
                    for item in data
                    if isinstance(item, dict) and "name" in item and "distinguished_name" in item
                ]
        except Exception as exc:
            logger.warning("Failed to retrieve cached AD OU tree from Redis: %s", exc)
        return None

    async def _set_cached_ou_tree(self, domain: str, users_root_ou: str, units: list[OrganizationalUnit]) -> None:
        if not units:
            return
        try:
            from core.redis_client import get_redis_client

            redis = get_redis_client()
            key = f"{OU_CACHE_KEY_PREFIX}:{domain.lower()}:{users_root_ou.strip().lower()}"
            payload = json.dumps(
                [{"name": u.name, "distinguished_name": u.distinguished_name} for u in units],
                ensure_ascii=False,
            )
            await redis.set(key, payload, ex=OU_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.warning("Failed to save AD OU tree to Redis cache: %s", exc)

    async def _get_cached_groups(self, domain: str) -> list[ActiveDirectoryGroup] | None:
        try:
            from core.redis_client import get_redis_client

            redis = get_redis_client()
            key = f"{GROUPS_CACHE_KEY_PREFIX}:{domain.lower()}"
            raw = await redis.get(key)
            if not raw:
                return None
            data = json.loads(raw)
            if isinstance(data, list):
                return [
                    ActiveDirectoryGroup(
                        name=item["name"],
                        sam_account_name=item["sam_account_name"],
                        distinguished_name=item["distinguished_name"],
                    )
                    for item in data
                    if isinstance(item, dict)
                    and "name" in item
                    and "sam_account_name" in item
                    and "distinguished_name" in item
                ]
        except Exception as exc:
            logger.warning("Failed to retrieve cached AD groups from Redis: %s", exc)
        return None

    async def _set_cached_groups(self, domain: str, groups: list[ActiveDirectoryGroup]) -> None:
        if not groups:
            return
        try:
            from core.redis_client import get_redis_client

            redis = get_redis_client()
            key = f"{GROUPS_CACHE_KEY_PREFIX}:{domain.lower()}"
            payload = json.dumps(
                [
                    {
                        "name": g.name,
                        "sam_account_name": g.sam_account_name,
                        "distinguished_name": g.distinguished_name,
                    }
                    for g in groups
                ],
                ensure_ascii=False,
            )
            await redis.set(key, payload, ex=GROUPS_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.warning("Failed to save AD groups to Redis cache: %s", exc)

    def _fetch_live_groups(self, conn: ldap3.Connection) -> list[ActiveDirectoryGroup]:
        groups_ou = os.getenv("AD_SERVICE_GROUPS_OU") or AD_SERVICE_DESK_GROUPS_OU
        try:
            conn.search(
                search_base=groups_ou,
                search_filter="(&(objectClass=group)(sAMAccountName=HLP_*))",
                search_scope=ldap3.SUBTREE,
                attributes=["sAMAccountName", "name", "distinguishedName"],
            )
            return [
                ActiveDirectoryGroup(
                    name=str(entry.name.value or entry.sAMAccountName.value),
                    sam_account_name=str(entry.sAMAccountName.value),
                    distinguished_name=str(entry.distinguishedName.value),
                )
                for entry in conn.entries
                if getattr(entry, "sAMAccountName", None) is not None
                and getattr(entry, "distinguishedName", None) is not None
            ]
        except Exception as exc:
            logger.warning("Failed to search live AD for HLP groups in '%s': %s", groups_ou, exc)
            return []

    def _load_groups_sync(
        self,
        conn: ldap3.Connection,
        cached_groups: list[ActiveDirectoryGroup] | None = None,
    ) -> tuple[list[ActiveDirectoryGroup], list[ActiveDirectoryGroup] | None]:
        if cached_groups is not None:
            return cached_groups, None
        fresh = self._fetch_live_groups(conn)
        return fresh, fresh

    def _resolve_target_ou_sync(
        self,
        conn: ldap3.Connection,
        *,
        users_root_ou: str,
        company: str,
        department: str,
        categories: str = "",
        applicant_company: str = "",
        applicant_department: str = "",
        applicant_login: str = "",
        target_ou_dn: str | None = None,
        cached_units: list[OrganizationalUnit] | None = None,
    ) -> tuple[str, dict, list[OrganizationalUnit] | None]:
        if target_ou_dn:
            target_ou = target_ou_dn.strip()
            if not _is_descendant(target_ou, users_root_ou):
                raise AccountProvisioningError(
                    "ad_target_ou_outside_root",
                    f"Selected OU '{target_ou}' is not within authorized root '{users_root_ou}'",
                )
            try:
                conn.search(
                    target_ou,
                    "(objectClass=organizationalUnit)",
                    ldap3.BASE,
                    attributes=["ou", "distinguishedName"],
                )
                if not conn.entries:
                    raise AccountProvisioningError(
                        "ad_target_ou_not_found",
                        f"Selected OU '{target_ou}' does not exist in Active Directory",
                    )
            except LDAPNoSuchObjectResult as err:
                raise AccountProvisioningError(
                    "ad_target_ou_not_found",
                    f"Selected OU '{target_ou}' does not exist in Active Directory",
                ) from err
            return target_ou, {
                "ou_resolution_method": "operator_confirmed",
                "resolved_company": company.strip() or None,
                "resolved_department": department.strip(),
                "resolved_categories": categories.strip() or None,
                "ou_candidates": [],
                "target_ou": target_ou,
            }, None

        resolved_app_dept = applicant_department.strip()
        resolved_app_comp = applicant_company.strip()
        if applicant_login.strip() and (not resolved_app_dept or not resolved_app_comp):
            safe_login = escape_filter_chars(applicant_login.strip())
            try:
                conn.search(
                    users_root_ou,
                    f"(&(objectClass=user)(sAMAccountName={safe_login}))",
                    ldap3.SUBTREE,
                    attributes=["department", "company"],
                )
                if conn.entries:
                    app_entry = conn.entries[0]
                    if not resolved_app_dept and getattr(app_entry, "department", None):
                        resolved_app_dept = str(app_entry.department.value or "").strip()
                    if not resolved_app_comp and getattr(app_entry, "company", None):
                        resolved_app_comp = str(app_entry.company.value or "").strip()
            except Exception:
                pass

        fresh_units: list[OrganizationalUnit] | None = None
        units = cached_units
        resolution = None

        if units is not None:
            resolution = self.ou_resolver.resolve(
                units,
                company=company,
                department=department,
                categories=categories,
                applicant_company=resolved_app_comp,
                applicant_department=resolved_app_dept,
                users_root_ou=users_root_ou,
            )

        if units is None or (resolution is not None and resolution.selected_ou is None):
            conn.search(
                users_root_ou,
                "(objectClass=organizationalUnit)",
                ldap3.SUBTREE,
                attributes=["ou", "distinguishedName"],
            )
            fresh_units = [
                OrganizationalUnit(
                    name=str(entry.ou.value or ""),
                    distinguished_name=str(entry.distinguishedName.value or ""),
                )
                for entry in conn.entries
                if getattr(entry, "ou", None) is not None and getattr(entry, "distinguishedName", None) is not None
            ]
            units = fresh_units
            resolution = self.ou_resolver.resolve(
                units,
                company=company,
                department=department,
                categories=categories,
                applicant_company=resolved_app_comp,
                applicant_department=resolved_app_dept,
                users_root_ou=users_root_ou,
            )

        details = {
            "ou_resolution_method": resolution.method,
            "resolved_company": company.strip() or None,
            "resolved_department": department.strip(),
            "resolved_categories": categories.strip() or None,
            "ou_candidates": [
                {
                    "distinguished_name": item.distinguished_name,
                    "name": item.name,
                    "score": item.score,
                    "evidence": list(item.evidence),
                }
                for item in resolution.candidates
            ],
        }
        if resolution.selected_ou is None:
            raise AccountProvisioningError(
                "ad_target_ou_ambiguous",
                "Target organizational unit could not be resolved unambiguously",
                details=details,
            )
        return resolution.selected_ou, details, fresh_units

    @staticmethod
    def _assert_ldap_success(conn: ldap3.Connection, step: str) -> None:
        result = conn.result or {}
        if result.get("result") != 0 and result.get("description") != "success":
            raise AccountProvisioningError(
                f"ldap_{step}_failed",
                f"LDAP step '{step}' failed: {result.get('description')} ({result.get('message')})",
            )

    def _create_and_verify_sync(
        self,
        *,
        last_name: str,
        first_name: str,
        middle_name: str,
        department: str,
        title: str,
        phone: str,
        company: str,
        password: str,
        categories: str = "",
        applicant_company: str = "",
        applicant_department: str = "",
        applicant_login: str = "",
        ad_pool: ActiveDirectoryPool | None = None,
        users_root_ou: str = AD_USERS_ROOT_OU,
        target_ou_dn: str | None = None,
        cached_units: list[OrganizationalUnit] | None = None,
        cached_groups: list[ActiveDirectoryGroup] | None = None,
    ) -> tuple[AccountProvisioningReceipt, list[OrganizationalUnit] | None, list[ActiveDirectoryGroup] | None]:
        full_name = f"{last_name} {first_name} {middle_name}".strip()
        pool = ad_pool or self.ad_pool
        if pool is None:
            raise AccountProvisioningError("ad_credentials_unavailable", "AD credentials are not configured")
        domain = pool.config.domain
        base_dn = ",".join(f"DC={part}" for part in domain.split(".") if part)

        with pool.connection_scope(auto_bind=True) as conn:
            target_ou, _resolution, fresh_units = self._resolve_target_ou_sync(
                conn,
                users_root_ou=users_root_ou,
                company=company,
                department=department,
                categories=categories,
                applicant_company=applicant_company,
                applicant_department=applicant_department,
                applicant_login=applicant_login,
                target_ou_dn=target_ou_dn,
                cached_units=cached_units,
            )
            user_dn = f"CN={escape_rdn(full_name)},{target_ou}"
            try:
                conn.search(
                    search_base=user_dn,
                    search_filter="(objectClass=user)",
                    search_scope=ldap3.BASE,
                    attributes=["distinguishedName"],
                )
                if conn.entries:
                    raise AccountProvisioningError(
                        "ad_object_already_exists",
                        "AD object for this employee already exists; automatic recreation is forbidden",
                    )
            except LDAPNoSuchObjectResult:
                pass

            collision_index = 1
            while True:
                sam = generate_sam_account_name(last_name, first_name, middle_name, collision_index)
                conn.search(
                    search_base=base_dn,
                    search_filter=(
                        "(&(objectClass=user)(sAMAccountName="
                        f"{escape_filter_chars(sam)}))"
                    ),
                    search_scope=ldap3.SUBTREE,
                    attributes=["sAMAccountName"],
                )
                if not conn.entries:
                    break
                collision_index += 1
                if collision_index > 50:
                    raise AccountProvisioningError(
                        "login_collision_limit",
                        "Active Directory login collision limit exceeded",
                    )

            upn = f"{sam}@{domain}"
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

            conn.add(user_dn, ["top", "person", "organizationalPerson", "user"], attributes)
            self._assert_ldap_success(conn, "create")
            created = True
            try:
                unicode_pwd = f'"{password}"'.encode("utf-16-le")
                conn.modify(user_dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [unicode_pwd])]})
                self._assert_ldap_success(conn, "set_password")

                conn.modify(user_dn, {"pwdLastSet": [(ldap3.MODIFY_REPLACE, [0])]})
                self._assert_ldap_success(conn, "force_password_change")

                conn.modify(user_dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [512])]})
                self._assert_ldap_success(conn, "enable_account")

                conn.search(
                    search_base=user_dn,
                    search_filter="(objectClass=user)",
                    search_scope=ldap3.BASE,
                    attributes=[
                        "sAMAccountName",
                        "userPrincipalName",
                        "distinguishedName",
                        "userAccountControl",
                        "pwdLastSet",
                    ],
                )
                if len(conn.entries) != 1:
                    raise AccountProvisioningError(
                        "ldap_verify_failed",
                        "Created AD object could not be read back",
                        ad_object_created=True,
                    )
                entry = conn.entries[0]
                verified_uac = int(entry.userAccountControl.value)
                if verified_uac & 0x0002:
                    raise AccountProvisioningError(
                        "ldap_verify_failed",
                        "Created AD account is still disabled",
                        ad_object_created=True,
                    )
                pwd_attr = getattr(entry, "pwdLastSet", None)
                is_pwd_zero = False
                if pwd_attr is not None:
                    raw = getattr(pwd_attr, "raw_values", [])
                    if raw and raw[0] in (b"0", "0", 0):
                        is_pwd_zero = True
                    else:
                        val = getattr(pwd_attr, "value", pwd_attr)
                        if isinstance(val, (int, str)):
                            try:
                                is_pwd_zero = int(val) == 0
                            except ValueError:
                                is_pwd_zero = False
                        elif hasattr(val, "year") and getattr(val, "year", None) == 1601:
                            is_pwd_zero = True
                if not is_pwd_zero:
                    raise AccountProvisioningError(
                        "ldap_verify_failed",
                        "Created AD account does not require a password change at first logon",
                        ad_object_created=True,
                    )
                if str(entry.sAMAccountName.value).casefold() != sam.casefold():
                    raise AccountProvisioningError(
                        "ldap_verify_failed",
                        "Created AD account login does not match requested login",
                        ad_object_created=True,
                    )
            except AccountProvisioningError as exc:
                if created and not exc.ad_object_created:
                    exc.ad_object_created = True
                raise
            except Exception as exc:
                raise AccountProvisioningError(
                    "ldap_step_exception",
                    "Unexpected LDAP failure after the AD object was created",
                    ad_object_created=True,
                ) from exc

            # Service group assignment (HLP_*)
            service_group_name: str | None = None
            service_group_added: bool = False
            service_group_error: str | None = None
            fresh_groups: list[ActiveDirectoryGroup] | None = None

            try:
                groups, fresh_groups_live = self._load_groups_sync(conn, cached_groups)
                if fresh_groups_live:
                    fresh_groups = fresh_groups_live
                group_res = self.group_resolver.resolve_service_group(
                    groups,
                    company=company,
                    target_ou_dn=target_ou,
                )
                if group_res.group is None and cached_groups is not None:
                    # Live AD fallback if group was created recently
                    live_groups = self._fetch_live_groups(conn)
                    if live_groups:
                        fresh_groups = live_groups
                        group_res = self.group_resolver.resolve_service_group(
                            live_groups,
                            company=company,
                            target_ou_dn=target_ou,
                        )

                if group_res.group is not None:
                    service_group_name = group_res.group.name
                    service_group_added, service_group_error = self.group_resolver.add_user_to_group_sync(
                        conn,
                        user_dn=user_dn,
                        group_dn=group_res.group.distinguished_name,
                    )
                else:
                    service_group_name = None
                    service_group_added = False
                    service_group_error = group_res.warning or "service_group_not_found"
            except Exception as grp_exc:
                logger.warning("Unexpected error during service group assignment: %s", grp_exc)
                service_group_name = None
                service_group_added = False
                service_group_error = str(grp_exc)

        return AccountProvisioningReceipt(
            sam_account_name=sam,
            upn=upn,
            user_dn=user_dn,
            full_name=full_name,
            credentials_written=False,
            service_group=service_group_name,
            service_group_added=service_group_added,
            service_group_error=service_group_error,
        ), fresh_units, fresh_groups

    def _preflight_sync(
        self,
        *,
        last_name: str,
        first_name: str,
        middle_name: str,
        department: str,
        company: str,
        categories: str = "",
        applicant_company: str = "",
        applicant_department: str = "",
        applicant_login: str = "",
        ad_pool: ActiveDirectoryPool | None = None,
        users_root_ou: str = AD_USERS_ROOT_OU,
        target_ou_dn: str | None = None,
        cached_units: list[OrganizationalUnit] | None = None,
        cached_groups: list[ActiveDirectoryGroup] | None = None,
    ) -> dict:
        """Read-only AD validation and deterministic login preview."""
        full_name = f"{last_name} {first_name} {middle_name}".strip()
        pool = ad_pool or self.ad_pool
        if pool is None:
            raise AccountProvisioningError("ad_credentials_unavailable", "AD credentials are not configured")
        domain = pool.config.domain
        base_dn = ",".join(f"DC={part}" for part in domain.split(".") if part)
        with pool.connection_scope(auto_bind=True, read_only=True) as conn:
            target_ou, resolution, fresh_units = self._resolve_target_ou_sync(
                conn,
                users_root_ou=users_root_ou,
                company=company,
                department=department,
                categories=categories,
                applicant_company=applicant_company,
                applicant_department=applicant_department,
                applicant_login=applicant_login,
                target_ou_dn=target_ou_dn,
                cached_units=cached_units,
            )
            user_dn = f"CN={escape_rdn(full_name)},{target_ou}"
            try:
                conn.search(user_dn, "(objectClass=user)", ldap3.BASE, attributes=["distinguishedName"])
                if conn.entries:
                    raise AccountProvisioningError("ad_object_already_exists", "AD object already exists")
            except LDAPNoSuchObjectResult:
                pass

            # Service group preview
            groups, fresh_groups = self._load_groups_sync(conn, cached_groups)
            group_res = self.group_resolver.resolve_service_group(
                groups,
                company=company,
                target_ou_dn=target_ou,
            )
            if group_res.group is None and cached_groups is not None:
                live_groups = self._fetch_live_groups(conn)
                if live_groups:
                    fresh_groups = live_groups
                    group_res = self.group_resolver.resolve_service_group(
                        live_groups,
                        company=company,
                        target_ou_dn=target_ou,
                    )
            preview_service_group = group_res.group.name if group_res.group else None
            service_group_dn = group_res.group.distinguished_name if group_res.group else None
            service_group_warning = group_res.warning

            for collision_index in range(1, 51):
                sam = generate_sam_account_name(last_name, first_name, middle_name, collision_index)
                conn.search(
                    base_dn,
                    f"(&(objectClass=user)(sAMAccountName={escape_filter_chars(sam)}))",
                    ldap3.SUBTREE,
                    attributes=["sAMAccountName"],
                )
                if not conn.entries:
                    return {
                        "target_ou": target_ou,
                        "preview_sam_account_name": sam,
                        "preview_upn": f"{sam}@{domain}",
                        "preview_user_dn": user_dn,
                        "preview_service_group": preview_service_group,
                        "service_group_dn": service_group_dn,
                        "service_group_warning": service_group_warning,
                        "_fresh_units": fresh_units,
                        "_fresh_groups": fresh_groups,
                        **resolution,
                    }
        raise AccountProvisioningError("login_collision_limit", "AD login collision limit exceeded")

    async def preflight(
        self,
        *,
        last_name: str,
        first_name: str,
        middle_name: str,
        department: str,
        company: str,
        categories: str = "",
        applicant_company: str = "",
        applicant_department: str = "",
        applicant_login: str = "",
        target_ou_dn: str | None = None,
    ) -> dict:
        pool, users_root_ou = await self._connection_boundary()
        cached_units = await self._get_cached_ou_tree(pool.config.domain, users_root_ou)
        cached_groups = await self._get_cached_groups(pool.config.domain)
        preview = await asyncio.to_thread(
            self._preflight_sync,
            last_name=last_name,
            first_name=first_name,
            middle_name=middle_name,
            department=department,
            company=company,
            categories=categories,
            applicant_company=applicant_company,
            applicant_department=applicant_department,
            applicant_login=applicant_login,
            ad_pool=pool,
            users_root_ou=users_root_ou,
            target_ou_dn=target_ou_dn,
            cached_units=cached_units,
            cached_groups=cached_groups,
        )
        fresh_units = preview.pop("_fresh_units", None)
        if fresh_units:
            await self._set_cached_ou_tree(pool.config.domain, users_root_ou, fresh_units)
        fresh_groups = preview.pop("_fresh_groups", None)
        if fresh_groups:
            await self._set_cached_groups(pool.config.domain, fresh_groups)
        await self.auth_bootstrap.bootstrap_auth(client=self.client)
        return preview

    async def provision(
        self,
        *,
        task_id: int,
        last_name: str,
        first_name: str,
        middle_name: str,
        department: str,
        title: str,
        phone: str,
        company: str,
        categories: str = "",
        applicant_company: str = "",
        applicant_department: str = "",
        applicant_login: str = "",
        delivery_policy: CredentialDeliveryPolicy | None = None,
        delivery_port: CredentialDeliveryPort | None = None,
        target_ou_dn: str | None = None,
    ) -> AccountProvisioningReceipt:
        pool, users_root_ou = await self._connection_boundary()
        cached_units = await self._get_cached_ou_tree(pool.config.domain, users_root_ou)
        cached_groups = await self._get_cached_groups(pool.config.domain)
        secret = generate_secure_password(length=14)
        password = secret.get_secret_value()
        del secret
        try:
            receipt, fresh_units, fresh_groups = await asyncio.to_thread(
                self._create_and_verify_sync,
                last_name=last_name,
                first_name=first_name,
                middle_name=middle_name,
                department=department,
                title=title,
                phone=phone,
                company=company,
                password=password,
                categories=categories,
                applicant_company=applicant_company,
                applicant_department=applicant_department,
                applicant_login=applicant_login,
                ad_pool=pool,
                users_root_ou=users_root_ou,
                target_ou_dn=target_ou_dn,
                cached_units=cached_units,
                cached_groups=cached_groups,
            )
            if fresh_units:
                await self._set_cached_ou_tree(pool.config.domain, users_root_ou, fresh_units)
            if fresh_groups:
                await self._set_cached_groups(pool.config.domain, fresh_groups)
        except AccountProvisioningError:
            password = ""
            raise
        except Exception as exc:
            password = ""
            raise AccountProvisioningError(
                "ldap_unexpected_error",
                "Unexpected LDAP failure before account creation was confirmed",
            ) from exc

        policy = delivery_policy or DEFAULT_AD_DELIVERY_POLICY
        port = delivery_port or self.delivery_port or IntraServiceCredentialDeliveryPort(
            client=self.client, auth_bootstrap=self.auth_bootstrap
        )

        try:
            delivery_result = await port.deliver(
                task_id=task_id,
                policy=policy,
                public_values={"user_login": receipt.sam_account_name},
                secret_values={"initial_password": password},
            )
        except Exception as exc:
            raise AccountProvisioningError(
                "credentials_delivery_unknown_outcome",
                f"Unexpected exception during credential delivery: {exc}",
                ad_object_created=True,
            ) from exc
        finally:
            password = ""

        if delivery_result.outcome == CredentialDeliveryOutcome.definitely_failed:
            raise AccountProvisioningError(
                "credentials_delivery_definitely_failed",
                f"Credential delivery rejected: {delivery_result.error_message}",
                ad_object_created=True,
                details={"error_code": delivery_result.error_code},
            )
        elif delivery_result.outcome == CredentialDeliveryOutcome.unknown_outcome:
            raise AccountProvisioningError(
                "credentials_delivery_unknown_outcome",
                f"Credential delivery outcome unknown: {delivery_result.error_message}",
                ad_object_created=True,
                details={"error_code": delivery_result.error_code},
            )

        return AccountProvisioningReceipt(
            sam_account_name=receipt.sam_account_name,
            upn=receipt.upn,
            user_dn=receipt.user_dn,
            full_name=receipt.full_name,
            credentials_written=True,
            delivery_receipt_id=delivery_result.receipt_id,
            service_group=receipt.service_group,
            service_group_added=receipt.service_group_added,
            service_group_error=receipt.service_group_error,
        )
