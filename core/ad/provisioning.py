"""Account provisioning boundary that never returns or logs plaintext credentials."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import ldap3
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from core.ad.credentials import AD_USERS_ROOT_OU, ActiveDirectoryCredentialVault
from core.ad.ou_resolver import OrganizationalUnit, OrganizationalUnitResolver
from core.ad.password import generate_secure_password
from core.ad.pool import ActiveDirectoryPool, ADPoolConfig
from core.ad.transliteration import generate_sam_account_name
from core.intraservice.auth import ServiceAuthBootstrap
from core.intraservice.client import IntraServiceClient

LOGIN_FIELD_ID = 1488
PASSWORD_FIELD_ID = 1489


@dataclass(frozen=True)
class AccountProvisioningReceipt:
    sam_account_name: str
    upn: str
    user_dn: str
    full_name: str
    credentials_written: bool = True


class AccountProvisioningError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        ad_object_created: bool = False,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.ad_object_created = ad_object_created
        self.details = details or {}


class AccountProvisioningService:
    """Create and verify an AD user, then atomically deliver credentials to ticket fields."""

    def __init__(
        self,
        ad_pool: ActiveDirectoryPool | None = None,
        client: IntraServiceClient | None = None,
        auth_bootstrap: ServiceAuthBootstrap | None = None,
        credential_vault: ActiveDirectoryCredentialVault | None = None,
    ) -> None:
        self.ad_pool = ad_pool
        self.client = client or IntraServiceClient()
        self.auth_bootstrap = auth_bootstrap or ServiceAuthBootstrap()
        self.credential_vault = credential_vault or ActiveDirectoryCredentialVault()
        self.ou_resolver = OrganizationalUnitResolver()

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

    def _resolve_target_ou_sync(
        self,
        conn: ldap3.Connection,
        *,
        users_root_ou: str,
        company: str,
        department: str,
    ) -> tuple[str, dict]:
        conn.search(
            users_root_ou,
            "(objectClass=organizationalUnit)",
            ldap3.SUBTREE,
            attributes=["ou", "distinguishedName"],
        )
        units = [
            OrganizationalUnit(
                name=str(entry.ou.value or ""),
                distinguished_name=str(entry.distinguishedName.value or ""),
            )
            for entry in conn.entries
            if getattr(entry, "ou", None) is not None and getattr(entry, "distinguishedName", None) is not None
        ]
        resolution = self.ou_resolver.resolve(
            units,
            company=company,
            department=department,
            users_root_ou=users_root_ou,
        )
        details = {
            "ou_resolution_method": resolution.method,
            "resolved_company": company.strip() or None,
            "resolved_department": department.strip(),
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
        return resolution.selected_ou, details

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
        ad_pool: ActiveDirectoryPool | None = None,
        users_root_ou: str = AD_USERS_ROOT_OU,
    ) -> AccountProvisioningReceipt:
        full_name = f"{last_name} {first_name} {middle_name}".strip()
        pool = ad_pool or self.ad_pool
        if pool is None:
            raise AccountProvisioningError("ad_credentials_unavailable", "AD credentials are not configured")
        domain = pool.config.domain
        base_dn = ",".join(f"DC={part}" for part in domain.split(".") if part)

        with pool.connection_scope(auto_bind=True) as conn:
            target_ou, _resolution = self._resolve_target_ou_sync(
                conn,
                users_root_ou=users_root_ou,
                company=company,
                department=department,
            )
            user_dn = f"CN={escape_rdn(full_name)},{target_ou}"
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
                verified_pwd_last_set = int(entry.pwdLastSet.value)
                if verified_pwd_last_set != 0:
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

        return AccountProvisioningReceipt(
            sam_account_name=sam,
            upn=upn,
            user_dn=user_dn,
            full_name=full_name,
            credentials_written=False,
        )

    def _preflight_sync(
        self,
        *,
        last_name: str,
        first_name: str,
        middle_name: str,
        department: str,
        company: str,
        ad_pool: ActiveDirectoryPool | None = None,
        users_root_ou: str = AD_USERS_ROOT_OU,
    ) -> dict:
        """Read-only AD validation and deterministic login preview."""
        full_name = f"{last_name} {first_name} {middle_name}".strip()
        pool = ad_pool or self.ad_pool
        if pool is None:
            raise AccountProvisioningError("ad_credentials_unavailable", "AD credentials are not configured")
        domain = pool.config.domain
        base_dn = ",".join(f"DC={part}" for part in domain.split(".") if part)
        with pool.connection_scope(auto_bind=True, read_only=True) as conn:
            target_ou, resolution = self._resolve_target_ou_sync(
                conn,
                users_root_ou=users_root_ou,
                company=company,
                department=department,
            )
            user_dn = f"CN={escape_rdn(full_name)},{target_ou}"
            conn.search(user_dn, "(objectClass=user)", ldap3.BASE, attributes=["distinguishedName"])
            if conn.entries:
                raise AccountProvisioningError("ad_object_already_exists", "AD object already exists")
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
    ) -> dict:
        pool, users_root_ou = await self._connection_boundary()
        preview = await asyncio.to_thread(
            self._preflight_sync,
            last_name=last_name,
            first_name=first_name,
            middle_name=middle_name,
            department=department,
            company=company,
            ad_pool=pool,
            users_root_ou=users_root_ou,
        )
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
    ) -> AccountProvisioningReceipt:
        pool, users_root_ou = await self._connection_boundary()
        secret = generate_secure_password(length=14)
        password = secret.get_secret_value()
        del secret
        try:
            receipt = await asyncio.to_thread(
                self._create_and_verify_sync,
                last_name=last_name,
                first_name=first_name,
                middle_name=middle_name,
                department=department,
                title=title,
                phone=phone,
                company=company,
                password=password,
                ad_pool=pool,
                users_root_ou=users_root_ou,
            )
        except AccountProvisioningError:
            password = ""
            raise
        except Exception as exc:
            password = ""
            raise AccountProvisioningError(
                "ldap_unexpected_error",
                "Unexpected LDAP failure before account creation was confirmed",
            ) from exc

        try:
            credentials = await self.auth_bootstrap.bootstrap_auth(client=self.client)
            await self.client.update_task(
                task_id=task_id,
                custom_fields={
                    LOGIN_FIELD_ID: receipt.sam_account_name,
                    PASSWORD_FIELD_ID: password,
                },
                auth_b64=credentials.auth_b64,
            )
        except Exception as exc:
            raise AccountProvisioningError(
                "credentials_delivery_failed_after_ad_create",
                "AD object was created, but ticket credential fields were not written",
                ad_object_created=True,
            ) from exc
        finally:
            password = ""

        return AccountProvisioningReceipt(
            sam_account_name=receipt.sam_account_name,
            upn=receipt.upn,
            user_dn=receipt.user_dn,
            full_name=receipt.full_name,
            credentials_written=True,
        )
