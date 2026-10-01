"""Explicit YAML-based AD OU Routing configuration, schema and validation."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.ad.credentials import AD_USERS_ROOT_OU
from core.ad.ou_resolver import (
    EXCLUDED_TOP_LEVEL_OUS,
    OrganizationalUnit,
    _is_descendant,
    _normalize,
    _relative_ou_names,
)
from core.intraservice.dto import TaskCategoryDTO

ROUTING_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "ad_ou_routing.yaml"


class CategoryRoutingMode(str, Enum):
    organization = "organization"
    branch_hint = "branch_hint"
    hint_only = "hint_only"
    review_required = "review_required"


class CategoryRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_names: list[str] = Field(default_factory=list)
    mode: CategoryRoutingMode
    organization_key: str | None = None
    branch_key: str | None = None

    @model_validator(mode="after")
    def validate_rule(self) -> "CategoryRule":
        if self.mode == CategoryRoutingMode.organization and not self.organization_key:
            raise ValueError("organization mode requires organization_key")
        if self.mode == CategoryRoutingMode.branch_hint and not self.branch_key:
            raise ValueError("branch_hint mode requires branch_key")
        return self


class ServiceRoutingRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: dict[int, CategoryRule] = Field(default_factory=dict)


class DepartmentRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aliases: list[str] = Field(default_factory=list)
    target_relative_dn: str


class OrganizationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aliases: list[str] = Field(default_factory=list)
    scope_dn: str
    departments: dict[str, DepartmentRule] = Field(default_factory=dict)


class AdOuRoutingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    services: dict[int, ServiceRoutingRule] = Field(default_factory=dict)
    organizations: dict[str, OrganizationRule] = Field(default_factory=dict)


def compute_routing_policy_hash(content_bytes: bytes) -> str:
    """Deterministic SHA-256 hash of the routing configuration file content."""
    return hashlib.sha256(content_bytes).hexdigest()


_CACHED_ROUTING_CONFIG: AdOuRoutingConfig | None = None
_CACHED_ROUTING_HASH: str | None = None
_CACHED_ROUTING_MTIME: float = 0.0


def load_routing_config(path: Path | None = None) -> tuple[AdOuRoutingConfig, str, int]:
    """Load and validate the AD OU routing configuration from YAML."""
    target_path = path or ROUTING_CONFIG_PATH
    if not target_path.exists():
        raise FileNotFoundError(f"AD OU routing config not found at: {target_path}")

    raw_bytes = target_path.read_bytes()
    config_hash = compute_routing_policy_hash(raw_bytes)
    data = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    config = AdOuRoutingConfig.model_validate(data)
    return config, config_hash, config.version


def get_cached_routing_config(path: Path | None = None) -> tuple[AdOuRoutingConfig, str, int]:
    """Return the cached routing config, refreshing if the file changed on disk."""
    global _CACHED_ROUTING_CONFIG, _CACHED_ROUTING_HASH, _CACHED_ROUTING_MTIME
    target_path = path or ROUTING_CONFIG_PATH
    if not target_path.exists():
        raise FileNotFoundError(f"AD OU routing config not found at: {target_path}")

    mtime = target_path.stat().st_mtime
    if _CACHED_ROUTING_CONFIG is None or mtime != _CACHED_ROUTING_MTIME:
        config, config_hash, version = load_routing_config(target_path)
        _CACHED_ROUTING_CONFIG = config
        _CACHED_ROUTING_HASH = config_hash
        _CACHED_ROUTING_MTIME = mtime
        return config, config_hash, version

    return _CACHED_ROUTING_CONFIG, _CACHED_ROUTING_HASH, _CACHED_ROUTING_CONFIG.version


def clear_routing_config_cache() -> None:
    """Clear memory cache for test isolation."""
    global _CACHED_ROUTING_CONFIG, _CACHED_ROUTING_HASH, _CACHED_ROUTING_MTIME
    _CACHED_ROUTING_CONFIG = None
    _CACHED_ROUTING_HASH = None
    _CACHED_ROUTING_MTIME = 0.0


class AdOuRoutingValidator:
    """Validates the AD OU routing configuration against live Active Directory and IntraService data."""

    def __init__(
        self,
        config: AdOuRoutingConfig,
        *,
        users_root_ou: str = AD_USERS_ROOT_OU,
        ad_units: Sequence[OrganizationalUnit] | None = None,
        service_categories: dict[int, list[TaskCategoryDTO]] | None = None,
    ) -> None:
        self.config = config
        self.users_root_ou = users_root_ou
        self.ad_units = ad_units or []
        self.service_categories = service_categories or {}
        self.ad_units_by_dn = {u.distinguished_name.casefold(): u for u in self.ad_units}

    def validate(self) -> list[str]:
        """Perform comprehensive validation and return a list of error strings."""
        errors: list[str] = []

        # 1. Uniqueness of keys and aliases
        alias_to_org: dict[str, str] = {}
        for org_key, org in self.config.organizations.items():
            for alias in org.aliases:
                norm_alias = _normalize(alias)
                if norm_alias in alias_to_org and alias_to_org[norm_alias] != org_key:
                    errors.append(
                        f"Duplicate organization alias '{alias}' in organizations '{alias_to_org[norm_alias]}' and '{org_key}'"
                    )
                alias_to_org[norm_alias] = org_key

            # Check department aliases uniqueness within organization
            dept_alias_to_key: dict[str, str] = {}
            for dept_key, dept in org.departments.items():
                for alias in dept.aliases:
                    norm_dept = _normalize(alias)
                    if norm_dept in dept_alias_to_key and dept_alias_to_key[norm_dept] != dept_key:
                        errors.append(
                            f"Duplicate department alias '{alias}' in org '{org_key}' (departments '{dept_alias_to_key[norm_dept]}' and '{dept_key}')"
                        )
                    dept_alias_to_key[norm_dept] = dept_key

        # 2. Scope DN existence, containment, and excluded branch checks
        for org_key, org in self.config.organizations.items():
            scope_dn = org.scope_dn.strip()
            if not _is_descendant(scope_dn, self.users_root_ou) or scope_dn.casefold() == self.users_root_ou.casefold():
                errors.append(
                    f"Organization '{org_key}' scope_dn '{scope_dn}' is not contained within users_root_ou '{self.users_root_ou}'"
                )
            rel_names = _relative_ou_names(scope_dn, self.users_root_ou)
            if rel_names and _normalize(rel_names[-1]) in EXCLUDED_TOP_LEVEL_OUS:
                errors.append(
                    f"Organization '{org_key}' scope_dn '{scope_dn}' is within an excluded branch"
                )

            if self.ad_units:
                if scope_dn.casefold() not in self.ad_units_by_dn:
                    errors.append(
                        f"Organization '{org_key}' scope_dn '{scope_dn}' does not exist in Active Directory tree"
                    )

            # Check department target_relative_dn
            for dept_key, dept in org.departments.items():
                if not dept.target_relative_dn.strip():
                    errors.append(f"Department '{dept_key}' in org '{org_key}' has empty target_relative_dn")
                elif self.ad_units:
                    full_dept_dn = f"{dept.target_relative_dn.strip()},{scope_dn}"
                    if full_dept_dn.casefold() not in self.ad_units_by_dn:
                        errors.append(
                            f"Department '{dept_key}' in org '{org_key}' target DN '{full_dept_dn}' does not exist in AD tree"
                        )

        # 3. Check service categories
        for service_id, service_rule in self.config.services.items():
            for cat_id, cat_rule in service_rule.categories.items():
                if not cat_rule.expected_names:
                    errors.append(f"Category {cat_id} in service {service_id} has no expected_names defined")

                if cat_rule.mode == CategoryRoutingMode.organization:
                    if cat_rule.organization_key not in self.config.organizations:
                        errors.append(
                            f"Category {cat_id} references unknown organization_key '{cat_rule.organization_key}'"
                        )

            # 4. Check coverage of all actual categories for the service if provided
            if service_id in self.service_categories:
                actual_cats = self.service_categories[service_id]
                configured_ids = set(service_rule.categories.keys())
                for cat in actual_cats:
                    if cat.id not in configured_ids:
                        errors.append(
                            f"Service {service_id} missing configuration for active IntraService category ID={cat.id} ({cat.name!r})"
                        )
                    else:
                        rule = service_rule.categories[cat.id]
                        norm_cat_name = _normalize(cat.name)
                        norm_expected = [_normalize(n) for n in rule.expected_names]
                        if norm_cat_name not in norm_expected:
                            errors.append(
                                f"Service {service_id} category ID={cat.id} name {cat.name!r} does not match any of expected_names: {rule.expected_names}"
                            )

        return errors


async def fetch_live_ad_units(users_root_ou: str | None = None) -> list[OrganizationalUnit]:
    """Fetch live OU tree from Active Directory using the configured pool / credentials."""
    import ldap3

    from core.ad.credentials import AD_USERS_ROOT_OU, ActiveDirectoryCredentialVault
    from core.ad.pool import ActiveDirectoryPool, ADPoolConfig, get_default_ad_pool

    root_ou = users_root_ou or AD_USERS_ROOT_OU
    vault = ActiveDirectoryCredentialVault()
    try:
        settings = await vault.load()
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
    except Exception:
        pool = get_default_ad_pool()

    with pool.connection_scope(auto_bind=True, read_only=True) as conn:
        conn.search(
            search_base=root_ou,
            search_filter="(objectClass=organizationalUnit)",
            search_scope=ldap3.SUBTREE,
            attributes=["ou", "distinguishedName"],
        )
        return [
            OrganizationalUnit(
                name=str(entry.ou.value or ""),
                distinguished_name=str(entry.distinguishedName.value or ""),
            )
            for entry in conn.entries
            if getattr(entry, "ou", None) is not None and getattr(entry, "distinguishedName", None) is not None
        ]


async def fetch_live_service_categories(service_id: int) -> list[TaskCategoryDTO]:
    """Fetch live categories for a service from IntraService API."""
    from core.intraservice.auth import ServiceAuthBootstrap
    from core.intraservice.client import IntraServiceClient

    client = IntraServiceClient()
    auth_bootstrap = ServiceAuthBootstrap()
    creds = await auth_bootstrap.bootstrap_auth(client=client)
    try:
        return await client.get_categories(service_id=service_id, auth_b64=creds.auth_b64)
    finally:
        await client.close()


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint to validate AD OU routing configuration."""
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(description="Validate Active Directory OU Routing Configuration")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROUTING_CONFIG_PATH,
        help="Path to ad_ou_routing.yaml (default: config/ad_ou_routing.yaml)",
    )
    parser.add_argument("--check-ad", action="store_true", help="Connect to Active Directory to verify OUs exist")
    parser.add_argument("--check-is", action="store_true", help="Connect to IntraService to verify category coverage")
    parser.add_argument("--service-id", type=int, default=53, help="IntraService service ID (default: 53)")
    parser.add_argument(
        "--users-root-ou",
        type=str,
        default=AD_USERS_ROOT_OU,
        help="Root OU for employee accounts",
    )
    args = parser.parse_args(argv)

    print(f"Loading configuration from {args.config}...")
    try:
        cfg, cfg_hash, version = load_routing_config(args.config)
        print(f"Loaded routing config version {version} (hash: {cfg_hash[:12]}...)")
    except Exception as exc:
        print(f"FAILED to parse configuration: {exc}", file=sys.stderr)
        return 1

    ad_units = None
    if args.check_ad:
        print("Connecting to Active Directory to fetch OU tree...")
        try:
            ad_units = asyncio.run(fetch_live_ad_units(args.users_root_ou))
            print(f"Fetched {len(ad_units)} OUs from AD.")
        except Exception as exc:
            print(f"FAILED to connect to Active Directory: {exc}", file=sys.stderr)
            return 1

    service_categories: dict[int, list[TaskCategoryDTO]] = {}
    if args.check_is:
        print(f"Connecting to IntraService to fetch categories for service {args.service_id}...")
        try:
            cats = asyncio.run(fetch_live_service_categories(args.service_id))
            service_categories[args.service_id] = cats
            print(f"Fetched {len(cats)} categories from IntraService.")
        except Exception as exc:
            print(f"FAILED to fetch categories from IntraService: {exc}", file=sys.stderr)
            return 1

    validator = AdOuRoutingValidator(
        config=cfg,
        users_root_ou=args.users_root_ou,
        ad_units=ad_units,
        service_categories=service_categories if service_categories else None,
    )
    errors = validator.validate()

    if errors:
        print(f"\nVALIDATION FAILED ({len(errors)} errors):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("\nVALIDATION SUCCESSFUL: Configuration is valid and consistent.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
