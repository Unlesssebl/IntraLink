"""Conservative YAML-driven and hierarchy-aware resolver for employee target OUs."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.ad.ou_routing import AdOuRoutingConfig, OrganizationRule

from core.intraservice.dto import normalize_category_pairs

EXCLUDED_TOP_LEVEL_OUS = frozenset(
    {
        "дубли учетных записей",
        "перенос",
        "service users",
        "wifi guest",
    }
)
BRANCH_CONTAINER_OUS = frozenset({"филиалы организаций"})
PREFIX_RE = re.compile(r"^[A-ZА-Я0-9\-]{2,6}$")
GENERIC_DEPARTMENT_WORDS = frozenset(
    {"цех", "отдел", "служба", "управление", "бюро", "группа", "сектор", "участок"}
)
MIN_OU_AUTO_SELECT_CONFIDENCE: float = 0.65
MIN_OU_AUTO_SELECT_MARGIN: float = 0.12
BRANCH_KEYWORDS: dict[str, tuple[str, ...]] = {
    "moscow": ("москва", "московск"),
    "ekaterinburg": ("екатеринбург",),
    "spb": ("санкт петербург", "петербург", "спб"),
    "polevskoy": ("полевской", "полевск"),
    "novosibirsk": ("новосибирск",),
    "koroleva": ("королева", "королев"),
}


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
    resolved_scope_dn: str | None = None

    @property
    def target_ou(self) -> str | None:
        return self.selected_ou

    @property
    def conflict_reason(self) -> str:
        return self.method

    @property
    def evidence(self) -> tuple[str, ...]:
        if self.candidates:
            return self.candidates[0].evidence
        return ()


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


def _extract_workshop_number(text: str) -> str | None:
    match = re.search(r"(?i)\b(?:цех[а-я]*|тпц)[\s\-№#]*(\d+)\b", text or "")
    return match.group(1) if match else None


def _ou_matches_workshop_number(ou_name: str, num: str) -> bool:
    norm = _normalize(ou_name)
    if "цех" not in norm and "тпц" not in norm:
        return False
    num_matches = re.findall(r"\b\d+\b", norm)
    return num in num_matches


def find_organization_by_alias(
    config: AdOuRoutingConfig,
    company_name: str,
) -> tuple[str, OrganizationRule] | None:
    """Find organization rule using exact canonical alias matching."""
    norm = _normalize(company_name)
    if not norm:
        return None
    for org_key, org_rule in config.organizations.items():
        for alias in org_rule.aliases:
            if _normalize(alias) == norm:
                return org_key, org_rule
    return None


def _find_department_candidates(
    scopes: Sequence[OrganizationRule],
    department: str,
    bounded: Sequence[OrganizationalUnit],
    cfg: AdOuRoutingConfig,
    users_root_ou: str,
    evidence: tuple[str, ...] = (),
) -> tuple[OuCandidate, ...]:
    if not department.strip():
        return ()
    norm_dept = _normalize(department)
    candidates: list[OuCandidate] = []
    seen_dns: set[str] = set()

    for org_rule in scopes:
        scope_dn = org_rule.scope_dn
        scope_units = [
            u
            for u in bounded
            if _is_descendant(u.distinguished_name, scope_dn)
            and u.distinguished_name.casefold() != scope_dn.casefold()
            and not _is_excluded_branch(u, users_root_ou)
        ]

        # Step A: Department alias in org_rule.departments
        found_in_aliases = False
        for dept_rule in org_rule.departments.values():
            for alias in dept_rule.aliases:
                if _normalize(alias) == norm_dept:
                    target_rdn = dept_rule.target_relative_dn.casefold()
                    matches = [
                        u
                        for u in scope_units
                        if u.distinguished_name.casefold().startswith(target_rdn + ",")
                        or u.distinguished_name.casefold() == f"{target_rdn},{scope_dn.casefold()}"
                        or _normalize(u.name) == _normalize(target_rdn.replace("ou=", ""))
                    ]
                    for m in matches:
                        if m.distinguished_name.casefold() not in seen_dns:
                            seen_dns.add(m.distinguished_name.casefold())
                            cand_ev = list(evidence) or ["organization_rule", "department_alias"]
                            candidates.append(
                                OuCandidate(
                                    distinguished_name=m.distinguished_name,
                                    name=m.name,
                                    score=0.9,
                                    evidence=tuple(cand_ev),
                                )
                            )
                            found_in_aliases = True
            if found_in_aliases:
                break

        if found_in_aliases:
            continue

        # Step B: Exact name in AD
        exact = [u for u in scope_units if _normalize(u.name) == norm_dept]
        for m in exact:
            if m.distinguished_name.casefold() not in seen_dns:
                seen_dns.add(m.distinguished_name.casefold())
                cand_ev = list(evidence) or ["department_exact"]
                candidates.append(
                    OuCandidate(
                        distinguished_name=m.distinguished_name,
                        name=m.name,
                        score=0.85,
                        evidence=tuple(cand_ev),
                    )
                )

        # Step C: Fuzzy matches inside scope if no exact match
        if not exact:
            fuzzy = []
            for u in scope_units:
                score = _similarity(u.name, department)
                if score >= MIN_OU_AUTO_SELECT_CONFIDENCE:
                    fuzzy.append((score, u))
            fuzzy.sort(key=lambda x: x[0], reverse=True)
            for score, m in fuzzy[:2]:
                if m.distinguished_name.casefold() not in seen_dns:
                    seen_dns.add(m.distinguished_name.casefold())
                    cand_ev = list(evidence) or ["fuzzy_department"]
                    candidates.append(
                        OuCandidate(
                            distinguished_name=m.distinguished_name,
                            name=m.name,
                            score=score,
                            evidence=tuple(cand_ev),
                        )
                    )

    return tuple(candidates)


class OrganizationalUnitResolver:
    """Conservative, YAML-driven hierarchy-aware resolver for employee target OUs."""

    def __init__(
        self,
        config: AdOuRoutingConfig | None = None,
        users_root_ou: str | None = None,
        routing_config: AdOuRoutingConfig | None = None,
    ) -> None:
        self.config = config or routing_config
        self.users_root_ou = users_root_ou

    def _get_config(self) -> AdOuRoutingConfig:
        if self.config is not None:
            return self.config
        from core.ad.ou_routing import get_cached_routing_config

        res = get_cached_routing_config()
        if res is None:
            raise RuntimeError("Active Directory OU routing config is not loaded")
        return res[0] if isinstance(res, tuple) else res

    def resolve(
        self,
        units: Iterable[OrganizationalUnit | str] | None = None,
        *,
        cached_ous: Iterable[OrganizationalUnit | str] | None = None,
        company: str = "",
        department: str = "",
        categories: str = "",
        applicant_company: str = "",
        applicant_department: str = "",
        users_root_ou: str | None = None,
        service_id: int = 53,
        routing_context: Any | None = None,
        config: AdOuRoutingConfig | None = None,
    ) -> OuResolution:
        from core.ad.ou_routing import CategoryRoutingMode, OrganizationRule

        cfg_raw = config or self.config or self._get_config()
        cfg: AdOuRoutingConfig = cfg_raw[0] if isinstance(cfg_raw, tuple) else cfg_raw

        users_root_ou = (users_root_ou or self.users_root_ou or "").strip() or "OU=Предприятия,DC=corp,DC=tempo"

        raw_units = units if units is not None else (cached_ous or [])
        parsed_units: list[OrganizationalUnit] = []
        for item in raw_units:
            if isinstance(item, str):
                u_name = item.split(",")[0].split("=")[-1] if "=" in item else item
                parsed_units.append(OrganizationalUnit(name=u_name, distinguished_name=item))
            else:
                parsed_units.append(item)

        bounded = [
            item
            for item in parsed_units
            if item.distinguished_name.casefold() != users_root_ou.casefold()
            and _is_descendant(item.distinguished_name, users_root_ou)
            and not _is_excluded_branch(item, users_root_ou)
        ]

        company = (company or "").strip()
        department = (department or "").strip()
        categories = (categories or "").strip()
        applicant_company = (applicant_company or "").strip()
        applicant_department = (applicant_department or "").strip()

        # Extract normalized categories from routing_context or parameters
        cat_items: list[tuple[int | None, str]] = []
        if routing_context is not None:
            raw_cats = (
                getattr(routing_context, "categories", None)
                or (routing_context.get("categories") if isinstance(routing_context, dict) else None)
                or []
            )
            for c in raw_cats:
                cid = getattr(c, "id", None) or (c.get("id") if isinstance(c, dict) else None)
                cname = getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else "")
                cat_items.append((cid, str(cname)))
            if not applicant_company:
                applicant_company = str(
                    getattr(routing_context, "applicant_company", None)
                    or (routing_context.get("applicant_company") if isinstance(routing_context, dict) else "")
                    or ""
                )
            if not applicant_department:
                applicant_department = str(
                    getattr(routing_context, "applicant_department", None)
                    or (routing_context.get("applicant_department") if isinstance(routing_context, dict) else "")
                    or ""
                )
        if not cat_items and categories.strip():
            pairs = normalize_category_pairs(categories=categories.strip())
            cat_items = [(p.id, p.name) for p in pairs]

        # Immediate child organization OUs under users_root_ou
        immediate_orgs = [
            u
            for u in bounded
            if len(_relative_ou_names(u.distinguished_name, users_root_ou)) == 1
        ]

        # 1. Resolve employee company strictly via exact canonical alias from YAML,
        # or exact match with an immediate child OU in AD
        emp_org_key: str | None = None
        emp_org_rule: OrganizationRule | None = None
        if company.strip():
            emp_res = find_organization_by_alias(cfg, company.strip())
            if emp_res is not None:
                emp_org_key, emp_org_rule = emp_res
            else:
                norm_comp = _normalize(company.strip())
                ad_matches = [u for u in immediate_orgs if _normalize(u.name) == norm_comp]
                if len(ad_matches) == 1:
                    emp_org_rule = OrganizationRule(
                        scope_dn=ad_matches[0].distinguished_name,
                        aliases=[ad_matches[0].name],
                    )

        # 2. Resolve categories strictly via category ID from YAML
        service_rule = cfg.services.get(service_id)
        cat_org_rules: list[OrganizationRule] = []
        branch_hint_key: str | None = None
        branch_hint_names: list[str] = []
        has_review_required = False

        if service_rule is not None:
            for cat_id, cat_name in cat_items:
                rule = None
                if cat_id is not None and cat_id in service_rule.categories:
                    rule = service_rule.categories[cat_id]
                    # Verify expected names
                    if rule.expected_names and cat_name.strip():
                        norm_cname = _normalize(cat_name)
                        if not any(_normalize(exp) == norm_cname for exp in rule.expected_names):
                            # Name mismatch: category ID and name diverge
                            continue
                elif cat_name.strip():
                    # Fallback lookup by exact expected_name when category_id is missing (legacy tests)
                    norm_cname = _normalize(cat_name)
                    for r in service_rule.categories.values():
                        if any(_normalize(exp) == norm_cname for exp in r.expected_names):
                            rule = r
                            break

                if rule is not None:
                    if rule.mode == CategoryRoutingMode.review_required:
                        has_review_required = True
                        if rule.organization_key and rule.organization_key in cfg.organizations:
                            cat_org_rules.append(cfg.organizations[rule.organization_key])
                    elif rule.mode == CategoryRoutingMode.organization and rule.organization_key:
                        if rule.organization_key in cfg.organizations:
                            cat_org_rules.append(cfg.organizations[rule.organization_key])
                    elif rule.mode == CategoryRoutingMode.branch_hint and rule.branch_key:
                        branch_hint_key = rule.branch_key
                        branch_hint_names = list(rule.expected_names) if rule.expected_names else []

        # Finding 3: Detect conflict between multiple organizational categories fail-closed
        distinct_cat_scopes: dict[str, OrganizationRule] = {}
        for r in cat_org_rules:
            distinct_cat_scopes[r.scope_dn.casefold()] = r

        if len(distinct_cat_scopes) > 1:
            conflict_candidates = _find_department_candidates(
                scopes=list(distinct_cat_scopes.values()),
                department=department,
                bounded=bounded,
                cfg=cfg,
                users_root_ou=users_root_ou,
                evidence=("category_scope_conflict",),
            )
            return OuResolution(None, "target_company_category_conflict", candidates=conflict_candidates)

        cat_org_rule: OrganizationRule | None = next(iter(distinct_cat_scopes.values())) if distinct_cat_scopes else None

        if has_review_required:
            rev_candidates: tuple[OuCandidate, ...] = ()
            target_scopes: list[OrganizationRule] = []
            if cat_org_rule is not None:
                target_scopes.append(cat_org_rule)
            elif emp_org_rule is not None:
                target_scopes.append(emp_org_rule)
            if target_scopes and department.strip():
                rev_candidates = _find_department_candidates(
                    scopes=target_scopes,
                    department=department,
                    bounded=bounded,
                    cfg=cfg,
                    users_root_ou=users_root_ou,
                    evidence=("category_mapping", "review_required"),
                )
            return OuResolution(
                None,
                "review_required_category",
                candidates=rev_candidates,
                resolved_scope_dn=cat_org_rule.scope_dn if cat_org_rule else (emp_org_rule.scope_dn if emp_org_rule else None),
            )

        # 3. If employee company and category point to different scopes -> bounded HITL
        if emp_org_rule is not None and cat_org_rule is not None:
            if emp_org_rule.scope_dn.casefold() != cat_org_rule.scope_dn.casefold():
                conflict_candidates = _find_department_candidates(
                    scopes=[emp_org_rule, cat_org_rule],
                    department=department,
                    bounded=bounded,
                    cfg=cfg,
                    users_root_ou=users_root_ou,
                    evidence=("target_company_category_conflict",),
                )
                return OuResolution(None, "target_company_category_conflict", candidates=conflict_candidates)

        # 4 & 5. Determine resolved organization scope
        resolved_org_rule: OrganizationRule | None = None
        org_evidence_code: str | None = None

        # Fail-closed if employee company was explicitly specified but cannot be resolved
        if company.strip() and emp_org_rule is None:
            return OuResolution(
                None,
                "target_company_unresolved",
                candidates=(),
                resolved_scope_dn=None,
            )

        if emp_org_rule is not None:
            resolved_org_rule = emp_org_rule
            org_evidence_code = "employee_company_mapping"
        elif cat_org_rule is not None:
            resolved_org_rule = cat_org_rule
            org_evidence_code = "category_mapping"
        elif not company.strip():
            # Fallback to applicant company ONLY if employee company was not supplied (empty)
            # and category is absent, unmapped or hint_only
            hierarchy = []
            if routing_context is not None:
                raw_h = (
                    getattr(routing_context, "applicant_company_hierarchy", None)
                    or (routing_context.get("applicant_company_hierarchy") if isinstance(routing_context, dict) else None)
                    or []
                )
                hierarchy = [str(x).strip() for x in raw_h if str(x).strip()]

            if applicant_company.strip():
                app_res = find_organization_by_alias(cfg, applicant_company.strip())
                if app_res is not None:
                    resolved_org_rule = app_res[1]
                    org_evidence_code = "applicant_company_fallback"
                else:
                    norm_app = _normalize(applicant_company.strip())
                    app_ad_matches = [u for u in immediate_orgs if _normalize(u.name) == norm_app]
                    if len(app_ad_matches) == 1:
                        resolved_org_rule = OrganizationRule(
                            scope_dn=app_ad_matches[0].distinguished_name,
                            aliases=[app_ad_matches[0].name],
                        )
                        org_evidence_code = "applicant_company_fallback"

            # If not resolved yet, check applicant_company_hierarchy
            if resolved_org_rule is None and hierarchy:
                for item in hierarchy:
                    app_res = find_organization_by_alias(cfg, item)
                    if app_res is not None:
                        resolved_org_rule = app_res[1]
                        org_evidence_code = "applicant_company_hierarchy_fallback"
                        break
                    else:
                        norm_item = _normalize(item)
                        h_matches = [u for u in immediate_orgs if _normalize(u.name) == norm_item]
                        if len(h_matches) == 1:
                            resolved_org_rule = OrganizationRule(
                                scope_dn=h_matches[0].distinguished_name,
                                aliases=[h_matches[0].name],
                            )
                            org_evidence_code = "applicant_company_hierarchy_fallback"
                            break

            # If applicant_department is empty and hierarchy is available, look for department in hierarchy
            if not applicant_department and hierarchy and resolved_org_rule is not None:
                for item in reversed(hierarchy):
                    if find_organization_by_alias(cfg, item) is None:
                        applicant_department = item
                        break

            # If no company/category was provided and exactly 1 organization exists in domain
            if (
                resolved_org_rule is None
                and not company.strip()
                and not applicant_company.strip()
                and len(immediate_orgs) == 1
            ):
                resolved_org_rule = OrganizationRule(
                    scope_dn=immediate_orgs[0].distinguished_name,
                    aliases=[immediate_orgs[0].name],
                )
                org_evidence_code = "single_domain_organization"

        if resolved_org_rule is None:
            return OuResolution(None, "organization_ambiguous", candidates=())

        resolved_scope_dn = resolved_org_rule.scope_dn

        # Candidates inside the confirmed organizational scope
        scope_units = [
            item
            for item in bounded
            if _is_descendant(item.distinguished_name, resolved_scope_dn)
            and item.distinguished_name.casefold() != resolved_scope_dn.casefold()
            and not _is_excluded_branch(item, users_root_ou)
        ]

        # Branch hint narrowing if present
        branch_evidence: str | None = None
        if branch_hint_key or branch_hint_names:
            branch_keywords: list[str] = []
            if branch_hint_key:
                branch_keywords.append(branch_hint_key.lower())
                if branch_hint_key in BRANCH_KEYWORDS:
                    branch_keywords.extend(BRANCH_KEYWORDS[branch_hint_key])
            for bname in branch_hint_names:
                norm_b = _normalize(bname)
                for w in norm_b.split():
                    if len(w) >= 4 and w not in ("филиал", "организаций", "предприятий"):
                        branch_keywords.append(w)

            # 1. Find branch roots matching branch keywords
            branch_roots = [
                u for u in scope_units
                if any(kw in _normalize(u.name) for kw in branch_keywords)
            ]
            if branch_roots:
                # If there are child units inside branch root(s), they are department candidates
                child_branch_units = [
                    u for u in scope_units
                    if any(
                        _is_descendant(u.distinguished_name, broot.distinguished_name)
                        and u.distinguished_name.casefold() != broot.distinguished_name.casefold()
                        for broot in branch_roots
                    )
                ]
                # If branch has sub-departments, use them; otherwise use the branch roots themselves
                branch_candidates = child_branch_units if child_branch_units else branch_roots
            else:
                # Fallback: search anywhere in distinguished_name
                branch_candidates = [
                    u for u in scope_units
                    if any(kw in _normalize(u.distinguished_name) for kw in branch_keywords)
                ]

            if not branch_candidates:
                return OuResolution(
                    None,
                    "branch_hint_unresolved",
                    candidates=(),
                    resolved_scope_dn=resolved_scope_dn,
                )

            scope_units = branch_candidates
            branch_evidence = "branch_mapping"

        if not department.strip():
            return OuResolution(None, "department_missing", candidates=(), resolved_scope_dn=resolved_scope_dn)

        evidence_base: list[str] = [org_evidence_code] if org_evidence_code else []
        if branch_evidence:
            evidence_base.append(branch_evidence)

        # 6. Fallback applicant company requires target department to be present
        norm_dept = _normalize(department)

        # Step A: Exact department aliases from YAML
        matched_dept_ou: OrganizationalUnit | None = None
        for dept_rule in resolved_org_rule.departments.values():
            for alias in dept_rule.aliases:
                if _normalize(alias) == norm_dept:
                    target_rdn = dept_rule.target_relative_dn.casefold()
                    matches = [
                        u for u in scope_units
                        if u.distinguished_name.casefold().startswith(target_rdn + ",")
                        or u.distinguished_name.casefold() == f"{target_rdn},{resolved_scope_dn.casefold()}"
                        or _normalize(u.name) == _normalize(target_rdn.replace("ou=", ""))
                    ]
                    if len(matches) == 1:
                        matched_dept_ou = matches[0]
                        break
            if matched_dept_ou is not None:
                break

        if matched_dept_ou is not None:
            evidence = list(evidence_base)
            evidence.append("department_alias")
            if applicant_department and _similarity(matched_dept_ou.name, applicant_department) >= 0.85:
                evidence.append("applicant_department_support")
            return OuResolution(
                matched_dept_ou.distinguished_name,
                "department_alias",
                (OuCandidate(matched_dept_ou.distinguished_name, matched_dept_ou.name, 1.0, tuple(evidence)),),
                resolved_scope_dn=resolved_scope_dn,
            )

        # Step B: Exact OU name in live AD
        exact = [item for item in scope_units if _normalize(item.name) == norm_dept]
        if len(exact) == 1:
            item = exact[0]
            evidence = list(evidence_base)
            evidence.append("department_exact")
            if applicant_department and _similarity(item.name, applicant_department) >= 0.85:
                evidence.append("applicant_department_support")
            return OuResolution(
                item.distinguished_name,
                "exact_hierarchy_match",
                (OuCandidate(item.distinguished_name, item.name, 1.0, tuple(evidence)),),
                resolved_scope_dn=resolved_scope_dn,
            )

        # Step C: Workshop number heuristic
        dept_num = _extract_workshop_number(department)
        if dept_num:
            matched_workshops = [item for item in scope_units if _ou_matches_workshop_number(item.name, dept_num)]
            if len(matched_workshops) == 1:
                item = matched_workshops[0]
                evidence = list(evidence_base)
                evidence.append("workshop_number_match")
                if applicant_department and _similarity(item.name, applicant_department) >= 0.85:
                    evidence.append("applicant_department_support")
                return OuResolution(
                    item.distinguished_name,
                    "workshop_number_match",
                    (OuCandidate(item.distinguished_name, item.name, 0.96, tuple(evidence)),),
                )
            elif len(matched_workshops) > 1:
                ranked_workshops = [
                    OuCandidate(
                        item.distinguished_name,
                        item.name,
                        0.96,
                        tuple(evidence_base + ["workshop_number_match"]),
                    )
                    for item in matched_workshops
                ][:5]
                return OuResolution(None, "department_ambiguous", tuple(ranked_workshops), resolved_scope_dn=resolved_scope_dn)

        # Step D: Compound prefix heuristic
        if norm_dept and norm_dept not in GENERIC_DEPARTMENT_WORDS:
            prefix_matches: list[tuple[OrganizationalUnit, str]] = []
            for item in scope_units:
                parts = item.name.strip().split()
                if len(parts) >= 2 and PREFIX_RE.match(parts[0]):
                    remainder = " ".join(parts[1:])
                    if _normalize(remainder) == norm_dept:
                        prefix_matches.append((item, parts[0]))

            if len(prefix_matches) == 1:
                item, prefix = prefix_matches[0]
                evidence = list(evidence_base)
                evidence.extend(["prefix_department_match", f"prefix:{prefix}"])
                if applicant_department and _similarity(item.name, applicant_department) >= 0.85:
                    evidence.append("applicant_department_support")
                return OuResolution(
                    item.distinguished_name,
                    "prefix_department_match",
                    (OuCandidate(item.distinguished_name, item.name, 0.98, tuple(evidence)),),
                    resolved_scope_dn=resolved_scope_dn,
                )

            dept_parts = department.strip().split()
            if len(dept_parts) >= 2 and PREFIX_RE.match(dept_parts[0]):
                dept_remainder = " ".join(dept_parts[1:])
                norm_rem = _normalize(dept_remainder)
                if norm_rem and norm_rem not in GENERIC_DEPARTMENT_WORDS:
                    rev_matches = [item for item in scope_units if _normalize(item.name) == norm_rem]
                    if len(rev_matches) == 1:
                        item = rev_matches[0]
                        evidence = list(evidence_base)
                        evidence.extend(["prefix_department_match", f"dept_prefix:{dept_parts[0]}"])
                        if applicant_department and _similarity(item.name, applicant_department) >= 0.85:
                            evidence.append("applicant_department_support")
                        return OuResolution(
                            item.distinguished_name,
                            "prefix_department_match",
                            (OuCandidate(item.distinguished_name, item.name, 0.98, tuple(evidence)),),
                            resolved_scope_dn=resolved_scope_dn,
                        )

        # Step E: Bounded Fuzzy Ranking only inside resolved scope
        candidates: list[OuCandidate] = []
        for item in scope_units:
            base_score = _similarity(item.name, department)
            evidence = list(evidence_base)
            evidence.append("department_similarity")
            score = base_score
            if applicant_department:
                app_score = _similarity(item.name, applicant_department)
                if app_score >= 0.85:
                    # Positive confirmation / tie-breaker only
                    score = min(1.0, score + 0.02)
                    evidence.append("applicant_department_support")
            candidates.append(
                OuCandidate(
                    distinguished_name=item.distinguished_name,
                    name=item.name,
                    score=round(score, 4),
                    evidence=tuple(evidence),
                )
            )

        ranked = sorted(candidates, key=lambda c: (-c.score, c.distinguished_name))
        top5 = tuple(ranked[:5])

        if not top5:
            return OuResolution(None, "department_ambiguous", (), resolved_scope_dn=resolved_scope_dn)

        runner_up_score = top5[1].score if len(top5) > 1 else 0.0
        margin = top5[0].score - runner_up_score

        # Auto-select at score >= MIN_OU_AUTO_SELECT_CONFIDENCE (65%) and margin >= MIN_OU_AUTO_SELECT_MARGIN
        if top5[0].score >= MIN_OU_AUTO_SELECT_CONFIDENCE and margin >= MIN_OU_AUTO_SELECT_MARGIN:
            return OuResolution(top5[0].distinguished_name, "department_similarity", top5, resolved_scope_dn=resolved_scope_dn)

        return OuResolution(None, "department_ambiguous", top5, resolved_scope_dn=resolved_scope_dn)


AdOuResolver = OrganizationalUnitResolver
AdOuResolution = OuResolution


def resolve_target_ou_with_routing(
    units: Iterable[OrganizationalUnit | str],
    *,
    company: str = "",
    department: str = "",
    categories: str = "",
    applicant_company: str = "",
    applicant_department: str = "",
    users_root_ou: str,
    service_id: int = 53,
    routing_context: Any | None = None,
    config: AdOuRoutingConfig | None = None,
) -> OuResolution:
    return OrganizationalUnitResolver(config=config).resolve(
        units=units,
        company=company,
        department=department,
        categories=categories,
        applicant_company=applicant_company,
        applicant_department=applicant_department,
        users_root_ou=users_root_ou,
        service_id=service_id,
        routing_context=routing_context,
        config=config,
    )
