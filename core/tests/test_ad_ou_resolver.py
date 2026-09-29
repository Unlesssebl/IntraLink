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
