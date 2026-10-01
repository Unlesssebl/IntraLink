"""Hierarchy and entity-aware resolver for Active Directory service groups (HLP_*)."""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

import ldap3

logger = logging.getLogger(__name__)

AD_SERVICE_DESK_GROUPS_OU = "OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"
GROUPS_CACHE_KEY_PREFIX = "ad:service_groups"
GROUPS_CACHE_TTL_SECONDS = 3600

# Containers that are intermediaries, not actual operating legal entities
EXCLUDED_ANCESTOR_CONTAINERS = frozenset(
    {
        "филиалы организаций",
        "service users",
        "corporate users",
        "дубли учетных записей",
        "перенос",
        "wifi guest",
    }
)

# Common generic corporate/holding words that should not trigger solitary matches
GENERIC_HOLDING_TOKENS = frozenset(
    {
        "тэмпо",
        "холдинг",
        "группа",
        "предприятие",
        "компания",
        "корпорация",
        "завод",
        "фабрика",
    }
)

OPF_TOKENS = frozenset({"ао", "ооо", "пао", "зао", "ип", "птфк", "нпо"})


@dataclass(frozen=True)
class ActiveDirectoryGroup:
    name: str
    sam_account_name: str
    distinguished_name: str


@dataclass(frozen=True)
class GroupResolution:
    group: ActiveDirectoryGroup | None
    method: str
    confidence: float
    warning: str | None = None


def normalize_entity_text(value: str) -> str:
    """Normalize company name, stripping quotes, dots in initials, and extra whitespace."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    # Strip quotes: « », " ", ' ', `, etc.
    text = re.sub(r"[\"\'«»„“”`]", " ", text)
    # Collapse dotted initials: e.g. "и.п." -> "ип", "э. ф." -> "эф", "э.ф." -> "эф"
    # Matches patterns like single russian letter followed by dot
    text = re.sub(r"\b([а-яa-z])\.\s*([а-яa-z])\.", r"\1\2", text)
    text = re.sub(r"\b([а-яa-z])\.", r"\1", text)
    # Replace non-alphanumeric chars with spaces
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return " ".join(text.split())


def _extract_tokens(value: str) -> list[str]:
    norm = normalize_entity_text(value)
    return [t for t in norm.split() if t]


def _distinctive_tokens(tokens: list[str]) -> list[str]:
    """Extract tokens excluding OPF and generic holding names, unless all tokens are generic."""
    distinctive = [t for t in tokens if t not in OPF_TOKENS and t not in GENERIC_HOLDING_TOKENS]
    if not distinctive:
        # Fallback to non-OPF tokens if only holding names present
        distinctive = [t for t in tokens if t not in OPF_TOKENS]
    return distinctive or tokens


def extract_organization_from_ou_dn(ou_dn: str) -> str | None:
    """Extract the operating organization OU name from a target OU distinguished name."""
    if not ou_dn:
        return None
    # DN parts: e.g. "OU=Отдел ИТ,OU=АО НТЗ,OU=Филиалы организаций,OU=CORPORATE_USERS,DC=corporate,DC=loc"
    parts = [p.strip() for p in ou_dn.split(",") if p.strip()]
    ou_names = [p[3:].strip() for p in parts if p.lower().startswith("ou=")]
    # Filter out top-level known system containers
    for name in reversed(ou_names):
        norm = normalize_entity_text(name)
        if norm and norm not in EXCLUDED_ANCESTOR_CONTAINERS:
            return name
    return None


class ActiveDirectoryGroupResolver:
    """Deterministic resolver for HLP_* corporate service groups."""

    @staticmethod
    def resolve_service_group(
        groups: Iterable[ActiveDirectoryGroup],
        *,
        company: str = "",
        organization_ou_name: str = "",
        target_ou_dn: str = "",
    ) -> GroupResolution:
        group_list = list(groups)
        if not group_list:
            return GroupResolution(
                group=None,
                method="empty_group_pool",
                confidence=0.0,
                warning="Список сервисных групп AD пуст",
            )

        # 0. Candidate organization names
        target_org = organization_ou_name.strip()
        if not target_org and target_ou_dn:
            target_org = extract_organization_from_ou_dn(target_ou_dn) or ""

        candidates_to_try: list[tuple[str, str]] = []
        if company.strip():
            candidates_to_try.append(("company_field", company.strip()))
        if target_org and normalize_entity_text(target_org) != normalize_entity_text(company):
            candidates_to_try.append(("organization_ou", target_org))

        if not candidates_to_try:
            return GroupResolution(
                group=None,
                method="no_company_provided",
                confidence=0.0,
                warning="Организация не указана и не определена по OU",
            )

        # 1. Exact match on HLP_<Name> (normalized)
        for source, name in candidates_to_try:
            norm_name = normalize_entity_text(name)
            # Match directly with group.name or sam_account_name
            for g in group_list:
                g_norm = normalize_entity_text(g.name)
                g_sam_norm = normalize_entity_text(g.sam_account_name)
                expected_norm = normalize_entity_text(f"HLP_{name}")
                if g_norm == expected_norm or g_sam_norm == expected_norm:
                    return GroupResolution(group=g, method=f"exact_match_{source}", confidence=1.0)
                # Also check without HLP_ prefix in group name if already prefixed
                if g_norm == f"hlp {norm_name}" or g_sam_norm == f"hlp {norm_name}":
                    return GroupResolution(group=g, method=f"exact_match_{source}", confidence=1.0)

        # 2. Token / Distinctive Stem Match (Smart Match)
        # Avoid matching container groups like "HLP_Филиалы организаций"
        valid_groups = [
            g
            for g in group_list
            if normalize_entity_text(g.name) not in {f"hlp {c}" for c in EXCLUDED_ANCESTOR_CONTAINERS}
        ]

        best_group: ActiveDirectoryGroup | None = None
        best_score = 0.0
        best_method = ""
        second_score = 0.0

        for source, name in candidates_to_try:
            raw_tokens = _extract_tokens(name)
            distinctive = _distinctive_tokens(raw_tokens)
            if not distinctive:
                continue

            for g in valid_groups:
                g_tokens = _extract_tokens(g.name)
                # Remove leading 'hlp' token from group tokens
                if g_tokens and g_tokens[0] == "hlp":
                    g_tokens = g_tokens[1:]
                g_distinctive = _distinctive_tokens(g_tokens)

                # Check if all distinctive tokens of the target company are in group tokens
                distinctive_match = all(t in g_tokens for t in distinctive)
                if not distinctive_match:
                    # Check substring match on distinctive tokens (e.g. "нтз" in "нтз")
                    distinctive_match = all(any(t == gt or t in gt for gt in g_distinctive) for t in distinctive)

                if distinctive_match:
                    # Calculate similarity between all non-generic tokens
                    name_norm = " ".join(raw_tokens)
                    g_clean_norm = " ".join(g_tokens)
                    ratio = SequenceMatcher(None, name_norm, g_clean_norm).ratio()
                    # Bonus if OPF matches
                    target_opf = {t for t in raw_tokens if t in OPF_TOKENS}
                    g_opf = {t for t in g_tokens if t in OPF_TOKENS}
                    if target_opf and target_opf == g_opf:
                        ratio += 0.1

                    score = min(1.0, 0.85 + 0.15 * ratio)
                    if score > best_score:
                        second_score = best_score
                        best_score = score
                        best_group = g
                        best_method = f"smart_distinctive_match_{source}"
                    elif score > second_score:
                        second_score = score

        if best_group is not None and best_score >= 0.85:
            # Check for ambiguity
            if best_score - second_score < 0.05 and second_score > 0.80:
                logger.warning(
                    "Service group match is ambiguous for '%s': best=%s (%.2f), second=%.2f",
                    company,
                    best_group.name,
                    best_score,
                    second_score,
                )
                return GroupResolution(
                    group=None,
                    method="ambiguous_match",
                    confidence=best_score,
                    warning=f"Найдено несколько подходящих групп HLP_* для '{company}'",
                )
            return GroupResolution(
                group=best_group,
                method=best_method,
                confidence=best_score,
            )

        return GroupResolution(
            group=None,
            method="no_matching_group",
            confidence=0.0,
            warning=f"Сервисная группа HLP_* для организации '{company or target_org}' не найдена",
        )

    @staticmethod
    def add_user_to_group_sync(
        conn: ldap3.Connection,
        user_dn: str,
        group_dn: str,
    ) -> tuple[bool, str | None]:
        """Atomically add user to AD group with idempotency and verification."""
        try:
            # 1. Check if user is already in group
            conn.search(
                search_base=user_dn,
                search_filter="(objectClass=user)",
                search_scope=ldap3.BASE,
                attributes=["memberOf"],
            )
            if conn.entries:
                existing_member_of = getattr(conn.entries[0], "memberOf", None)
                if existing_member_of is not None:
                    member_of_values = [str(v).casefold() for v in existing_member_of.values]
                    if group_dn.casefold() in member_of_values:
                        logger.info("User '%s' is already a member of group '%s'", user_dn, group_dn)
                        return True, None

            # 2. Modify group member attribute
            conn.modify(group_dn, {"member": [(ldap3.MODIFY_ADD, [user_dn])]})
            res = conn.result or {}
            res_code = res.get("result", 0)
            res_desc = str(res.get("description", ""))
            res_msg = str(res.get("message", ""))

            # Handle success or entryAlreadyExists (code 68)
            if res_code == 0:
                logger.info("Successfully added user '%s' to group '%s'", user_dn, group_dn)
                return True, None
            if res_code == 68 or "entryalreadyexists" in res_desc.lower() or "type or value exists" in res_msg.lower():
                logger.info("User '%s' already exists in group '%s' (LDAP 68)", user_dn, group_dn)
                return True, None

            err = f"LDAP error {res_code}: {res_desc} ({res_msg})"
            logger.warning("Failed to add user '%s' to group '%s': %s", user_dn, group_dn, err)
            return False, err
        except Exception as exc:
            err = f"Exception during LDAP group member add: {exc}"
            logger.warning(err)
            return False, err
