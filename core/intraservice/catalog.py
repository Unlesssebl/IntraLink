"""IntraService service catalog hierarchy, section definitions and business invariants (GEMINI.md)."""

from typing import Dict, List, Optional

from core.intraservice.dto import ServiceDTO

# Section 05: DIRECTUM & B2B Tenders
DIRECTUM_SECTION_ID = 5
DIRECTUM_TASK_TYPE_GENERAL = 4  # General Directum (incidents, contracts, tenders)
DIRECTUM_TASK_TYPE_ONBOARDING = 1018  # Directum User Account onboarding (HR questionnaire)

# Directum Service mapping by Task Type (GEMINI.md)
DIRECTUM_TYPE_4_SERVICES = {40, 41, 233, 234}
DIRECTUM_TYPE_1018_SERVICES = {55, 232}

# Specific Service IDs in Section 05
SERVICE_DIRECTUM_CONTRACTS = 40  # Согласование договоров, счетов и документов
SERVICE_DIRECTUM_TENDERS = 41  # Закупки и тендеры (B2B-Center)
SERVICE_DIRECTUM_ACCESS = 232  # Выдача доступа, сброс паролей
SERVICE_DIRECTUM_INSTALL = 233  # Установка Directum
SERVICE_DIRECTUM_TECH = 234  # Технические проблемы Directum
SERVICE_DIRECTUM_ONBOARDING = 55  # Заявка на пользователя Directum

# Section 09: Electronic Digital Signature (EDS) & Bank Clients
EDS_SECTION_ID = 9

# Section 06: 1C ERP & Enterprise Questions
SECTION_1C_ID = 6

# Canonical Service ID clusters for first-line autopilot scenarios
SERVICE_IDS_PRINTER_INSTALL = {62, 82, 83, 183}
SERVICE_IDS_PRINTER_SUPPORT = {12, 19, 62, 82, 83, 183}
SERVICE_IDS_ACCOUNT_CREATE = {55, 232}
SERVICE_IDS_ACCOUNT_LOCK = {8}
SERVICE_IDS_WLAN = {63}
SERVICE_IDS_OFFLINE_HOST = {112, 71}



class ServiceCatalog:
    """In-memory service catalog representation with fast lookups."""

    def __init__(self, services: Optional[List[ServiceDTO]] = None) -> None:
        self._services_by_id: Dict[int, ServiceDTO] = {}
        self._children_by_parent: Dict[int, List[int]] = {}
        if services:
            self.load(services)

    def load(self, services: List[ServiceDTO]) -> None:
        """Populate catalog index from list of services."""
        self._services_by_id.clear()
        self._children_by_parent.clear()
        for s in services:
            self._services_by_id[s.id] = s
            parent = s.parent_id or 0
            self._children_by_parent.setdefault(parent, []).append(s.id)

    def get_service(self, service_id: int) -> Optional[ServiceDTO]:
        return self._services_by_id.get(service_id)

    def get_service_path(self, service_id: int) -> str:
        """Construct full hierarchical path, e.g. '05. DIRECTUM / Технические проблемы'."""
        path_parts: List[str] = []
        curr_id: Optional[int] = service_id

        while curr_id and curr_id in self._services_by_id:
            svc = self._services_by_id[curr_id]
            path_parts.append(svc.name)
            curr_id = svc.parent_id

        return " / ".join(reversed(path_parts))

    @staticmethod
    def get_expected_task_type(service_id: int) -> Optional[int]:
        """Determine required IntraService TaskTypeId for section 05 according to GEMINI.md."""
        if service_id in DIRECTUM_TYPE_4_SERVICES:
            return DIRECTUM_TASK_TYPE_GENERAL
        if service_id in DIRECTUM_TYPE_1018_SERVICES:
            return DIRECTUM_TASK_TYPE_ONBOARDING
        return None
