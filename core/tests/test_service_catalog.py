"""Test service catalog invariants according to GEMINI.md."""

from core.intraservice.catalog import (
    DIRECTUM_TASK_TYPE_GENERAL,
    DIRECTUM_TASK_TYPE_ONBOARDING,
    SERVICE_DIRECTUM_ACCESS,
    SERVICE_DIRECTUM_CONTRACTS,
    SERVICE_DIRECTUM_INSTALL,
    SERVICE_DIRECTUM_ONBOARDING,
    SERVICE_DIRECTUM_TECH,
    SERVICE_DIRECTUM_TENDERS,
    ServiceCatalog,
)
from core.intraservice.dto import ServiceDTO


def test_directum_task_type_invariants():
    """Verify GEMINI.md Section 05 rules:
    Services 40, 41, 233, 234 -> TaskType 4
    Services 55, 232 -> TaskType 1018
    """
    assert ServiceCatalog.get_expected_task_type(SERVICE_DIRECTUM_CONTRACTS) == DIRECTUM_TASK_TYPE_GENERAL
    assert ServiceCatalog.get_expected_task_type(SERVICE_DIRECTUM_TENDERS) == DIRECTUM_TASK_TYPE_GENERAL
    assert ServiceCatalog.get_expected_task_type(SERVICE_DIRECTUM_INSTALL) == DIRECTUM_TASK_TYPE_GENERAL
    assert ServiceCatalog.get_expected_task_type(SERVICE_DIRECTUM_TECH) == DIRECTUM_TASK_TYPE_GENERAL

    assert ServiceCatalog.get_expected_task_type(SERVICE_DIRECTUM_ACCESS) == DIRECTUM_TASK_TYPE_ONBOARDING
    assert ServiceCatalog.get_expected_task_type(SERVICE_DIRECTUM_ONBOARDING) == DIRECTUM_TASK_TYPE_ONBOARDING

    # Other service outside Directum section
    assert ServiceCatalog.get_expected_task_type(9999) is None


def test_service_catalog_path_construction():
    services = [
        ServiceDTO(Id=1, Name="ИТ отдел", ParentId=None),
        ServiceDTO(Id=5, Name="05. DIRECTUM", ParentId=1),
        ServiceDTO(Id=234, Name="Технические проблемы", ParentId=5),
    ]
    catalog = ServiceCatalog(services)

    path = catalog.get_service_path(234)
    assert path == "ИТ отдел / 05. DIRECTUM / Технические проблемы"
