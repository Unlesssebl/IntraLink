"""Conservative hierarchy-aware resolver for employee target OUs."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

EXCLUDED_TOP_LEVEL_OUS = frozenset(
    {
        "дубли учетных записей",
        "перенос",
        "service users",
        "wifi guest",
    }
)
BRANCH_CONTAINER_OUS = frozenset({"филиалы организаций"})


@dataclass(frozen=True)
class OrganizationalUnit:
    name: str
    distinguished_name: str


@dataclass(frozen=True)
class OuCandidate:
    distinguished_name: str
    name: str
    score: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class OuResolution:
    selected_ou: str | None
    method: str
    candidates: tuple[OuCandidate, ...]


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", text).split())


def _similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    a_tokens, b_tokens = set(a.split()), set(b.split())
    jaccard = len(a_tokens & b_tokens) / max(1, len(a_tokens | b_tokens))
    return max(jaccard, SequenceMatcher(None, a, b).ratio())


def _is_descendant(distinguished_name: str, ancestor: str) -> bool:
    dn = distinguished_name.casefold()
    root = ancestor.casefold()
    return dn == root or dn.endswith("," + root)


def _split_dn(distinguished_name: str) -> tuple[str, ...]:
    """Split an LDAP DN without treating escaped commas as separators."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for character in distinguished_name:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            current.append(character)
            escaped = True
        elif character == ",":
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    parts.append("".join(current).strip())
    return tuple(part for part in parts if part)


def _relative_ou_names(distinguished_name: str, users_root_ou: str) -> tuple[str, ...]:
    dn_parts = _split_dn(distinguished_name)
    root_parts = _split_dn(users_root_ou)
    if len(dn_parts) < len(root_parts):
        return ()
    if tuple(part.casefold() for part in dn_parts[-len(root_parts) :]) != tuple(
        part.casefold() for part in root_parts
    ):
        return ()
    values: list[str] = []
    for rdn in dn_parts[: -len(root_parts)]:
        if rdn.casefold().startswith("ou="):
            values.append(rdn[3:])
    return tuple(values)


def _is_excluded_branch(item: OrganizationalUnit, users_root_ou: str) -> bool:
    relative = _relative_ou_names(item.distinguished_name, users_root_ou)
    if not relative:
        return False
    top_level = _normalize(relative[-1])
    return top_level in EXCLUDED_TOP_LEVEL_OUS


def _is_organization_candidate(item: OrganizationalUnit, users_root_ou: str) -> bool:
    relative = _relative_ou_names(item.distinguished_name, users_root_ou)
    if len(relative) == 1:
        normalized = _normalize(item.name)
        return normalized not in EXCLUDED_TOP_LEVEL_OUS and normalized not in BRANCH_CONTAINER_OUS
    return len(relative) == 2 and _normalize(relative[-1]) in BRANCH_CONTAINER_OUS


def _is_branch_container(item: OrganizationalUnit, users_root_ou: str) -> bool:
    relative = _relative_ou_names(item.distinguished_name, users_root_ou)
    return len(relative) == 1 and _normalize(item.name) in BRANCH_CONTAINER_OUS


class OrganizationalUnitResolver:
    """Resolve only a high-confidence OU from bounded live AD candidates."""

    def resolve(
        self,
        units: Iterable[OrganizationalUnit],
        *,
        company: str,
        department: str,
        users_root_ou: str,
    ) -> OuResolution:
        bounded = [
            item
            for item in units
            if item.distinguished_name.casefold() != users_root_ou.casefold()
            and _is_descendant(item.distinguished_name, users_root_ou)
            and not _is_excluded_branch(item, users_root_ou)
        ]
        organizations = [item for item in bounded if _is_organization_candidate(item, users_root_ou)]
        organization: OrganizationalUnit | None = None
        if company.strip():
            org_exact = [item for item in organizations if _normalize(item.name) == _normalize(company)]
            if len(org_exact) == 1:
                organization = org_exact[0]
            else:
                ranked_organizations = self._rank(organizations, company, "company")
                runner_up = ranked_organizations[1].score if len(ranked_organizations) > 1 else 0.0
                if (
                    ranked_organizations
                    and ranked_organizations[0].score >= 0.93
                    and ranked_organizations[0].score - runner_up >= 0.15
                ):
                    selected_dn = ranked_organizations[0].distinguished_name
                    organization = next(item for item in organizations if item.distinguished_name == selected_dn)
                else:
                    return OuResolution(None, "organization_ambiguous", ranked_organizations)
            bounded = [
                item
                for item in bounded
                if item.distinguished_name.casefold() != organization.distinguished_name.casefold()
                and _is_descendant(item.distinguished_name, organization.distinguished_name)
            ]
        else:
            organization_dns = {item.distinguished_name.casefold() for item in organizations}
            bounded = [
                item
                for item in bounded
                if item.distinguished_name.casefold() not in organization_dns
                and not _is_branch_container(item, users_root_ou)
            ]

        exact = [item for item in bounded if _normalize(item.name) == _normalize(department)]
        if len(exact) == 1:
            item = exact[0]
            evidence = ["department_exact"]
            if organization is not None:
                evidence.append("organization_ancestor_match")
            return OuResolution(
                item.distinguished_name,
                "exact_hierarchy_match",
                (OuCandidate(item.distinguished_name, item.name, 1.0, tuple(evidence)),),
            )
        ranked = self._rank(bounded, department, "department")
        if ranked and ranked[0].score >= 0.92:
            runner_up = ranked[1].score if len(ranked) > 1 else 0.0
            if ranked[0].score - runner_up >= 0.12:
                return OuResolution(ranked[0].distinguished_name, "high_confidence_name_match", ranked)
        return OuResolution(None, "department_ambiguous", ranked)

    @staticmethod
    def _rank(units: Iterable[OrganizationalUnit], value: str, source: str) -> tuple[OuCandidate, ...]:
        candidates = [
            OuCandidate(
                distinguished_name=item.distinguished_name,
                name=item.name,
                score=round(_similarity(item.name, value), 4),
                evidence=(f"{source}_name_similarity",),
            )
            for item in units
        ]
        return tuple(sorted(candidates, key=lambda item: (-item.score, item.distinguished_name))[:5])
