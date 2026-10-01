"""Hierarchy and ambiguity checks for Active Directory OU routing."""

import pytest

from core.ad.credentials import AD_USERS_ROOT_OU
from core.ad.ou_resolver import OrganizationalUnit, OrganizationalUnitResolver


def _ou(name: str, parent: str = AD_USERS_ROOT_OU) -> OrganizationalUnit:
    return OrganizationalUnit(name=name, distinguished_name=f"OU={name},{parent}")


def test_resolves_department_only_inside_exact_organization() -> None:
    first = _ou("АО КЗМК ТЭМПО")
    second = _ou("АО НТЗ ТЭМПО")
    units = [
        first,
        second,
        _ou("Отдел кадров", first.distinguished_name),
        _ou("Отдел кадров", second.distinguished_name),
    ]

    result = OrganizationalUnitResolver().resolve(
        units,
        company="АО КЗМК ТЭМПО",
        department="Отдел кадров",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    assert result.method == "exact_hierarchy_match"
    assert result.selected_ou == f"OU=Отдел кадров,{first.distinguished_name}"


def test_duplicate_department_without_company_fails_closed() -> None:
    first = _ou("АО КЗМК ТЭМПО")
    second = _ou("АО НТЗ ТЭМПО")
    result = OrganizationalUnitResolver().resolve(
        [
            first,
            second,
            _ou("Руководство", first.distinguished_name),
            _ou("Руководство", second.distinguished_name),
        ],
        company="",
        department="Руководство",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    assert result.selected_ou is None
    assert result.method == "department_ambiguous"


@pytest.mark.parametrize("branch_name", ["SERVICE_USERS", "WIFI GUEST", "!ПЕРЕНОС", "!Дубли учётных записей"])
def test_service_branches_are_never_target_candidates(branch_name: str) -> None:
    service_users = _ou(branch_name)
    result = OrganizationalUnitResolver().resolve(
        [service_users, _ou("Automation", service_users.distinguished_name)],
        company=branch_name,
        department="Automation",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    assert result.selected_ou is None
    assert result.method == "organization_ambiguous"
    assert result.candidates == ()


def test_organization_below_affiliates_container_is_supported() -> None:
    affiliates = _ou("Филиалы организаций")
    branch = _ou("ООО Тестовый филиал", affiliates.distinguished_name)
    department = _ou("Бухгалтерия", branch.distinguished_name)

    result = OrganizationalUnitResolver().resolve(
        [affiliates, branch, department],
        company="ООО Тестовый филиал",
        department="Бухгалтерия",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    assert result.selected_ou == department.distinguished_name
    assert result.method == "exact_hierarchy_match"


def test_resolves_organization_by_categories_when_company_empty() -> None:
    first = _ou("АО КЗМК ТЭМПО")
    second = _ou("АО НТЗ ТЭМПО")
    dept = _ou("Отдел кадров", second.distinguished_name)

    result = OrganizationalUnitResolver().resolve(
        [first, second, dept],
        company="",
        categories='АО "НТЗ "ТЭМ-ПО"',
        department="Отдел кадров",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    assert result.selected_ou == dept.distinguished_name
    assert result.method == "exact_hierarchy_match"
    assert "organization_ancestor_match:categories" in result.candidates[0].evidence


def test_resolves_organization_by_applicant_company_when_others_empty() -> None:
    first = _ou("АО КЗМК ТЭМПО")
    second = _ou("АО НТЗ ТЭМПО")
    dept = _ou("Бухгалтерия", first.distinguished_name)

    result = OrganizationalUnitResolver().resolve(
        [first, second, dept],
        company="",
        categories="",
        applicant_company="АО КЗМК ТЭМПО",
        department="Бухгалтерия",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    assert result.selected_ou == dept.distinguished_name
    assert result.method == "exact_hierarchy_match"
    assert "organization_ancestor_match:applicant_company" in result.candidates[0].evidence


def test_resolves_workshop_by_number() -> None:
    org = _ou("АО НТЗ ТЭМПО")
    workshop1 = _ou("Цех по изготовлению труб 1", org.distinguished_name)
    workshop4 = _ou("Цех по изготовлению труб 4", org.distinguished_name)
    other = _ou("Склад готовой продукции", org.distinguished_name)

    result = OrganizationalUnitResolver().resolve(
        [org, workshop1, workshop4, other],
        company="АО НТЗ ТЭМПО",
        department="цеха №4",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    assert result.selected_ou == workshop4.distinguished_name
    assert result.method == "workshop_number_match"
    assert result.candidates[0].score == 0.96
    assert "workshop_number_match" in result.candidates[0].evidence


def test_workshop_number_ambiguity_fails_closed() -> None:
    org = _ou("АО НТЗ ТЭМПО")
    pipe4 = _ou("Цех по изготовлению труб 4", org.distinguished_name)
    roll4 = _ou("Цех горячего проката 4", org.distinguished_name)

    result = OrganizationalUnitResolver().resolve(
        [org, pipe4, roll4],
        company="АО НТЗ ТЭМПО",
        department="цех 4",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    # Since there are 2 workshops with number 4, it must fail-closed to avoid wrong assignment
    assert result.selected_ou is None
    assert result.method == "department_ambiguous"


def test_applicant_department_alignment_boosts_similarity() -> None:
    org = _ou("АО НТЗ ТЭМПО")
    target_dept = _ou("Отдел материально технического снабжения", org.distinguished_name)
    other_dept = _ou("Отдел технического контроля", org.distinguished_name)

    result = OrganizationalUnitResolver().resolve(
        [org, target_dept, other_dept],
        company="АО НТЗ ТЭМПО",
        department="ОМТС снабжение",
        applicant_department="Отдел материально технического снабжения",
        users_root_ou=AD_USERS_ROOT_OU,
    )

    # Target dept matches applicant department, boost should be reflected in evidence
    assert any("applicant_department_alignment" in c.evidence for c in result.candidates)
