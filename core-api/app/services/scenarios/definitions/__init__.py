"""Built-in domain scenario definitions for Scenario Engine 2.0."""

from __future__ import annotations

from app.services.scenarios.base import Scenario
from app.services.scenarios.definitions.consultation import ConsultationScenario
from app.services.scenarios.definitions.create_user import CreateUserScenario
from app.services.scenarios.definitions.file_lock import FileLockScenario
from app.services.scenarios.definitions.grant_wlan import build_grant_wlan_scenario
from app.services.scenarios.definitions.hardware_repair import HardwareRepairScenario
from app.services.scenarios.definitions.install_printer import build_install_printer_scenarios
from app.services.scenarios.definitions.network_diag import NetworkDiagnosticsScenario
from app.services.scenarios.definitions.offline_host import OfflineHostScenario
from app.services.scenarios.definitions.os_reinstall import OSReinstallationScenario
from app.services.scenarios.definitions.pc_performance import PCPerformanceScenario
from app.services.scenarios.definitions.peripheral import build_peripheral_scenarios
from app.services.scenarios.definitions.printer_failures import build_printer_failure_scenarios
from app.services.scenarios.definitions.rag_consultation import RAGConsultationScenario
from app.services.scenarios.definitions.service_redirect import ServiceRedirectScenario


def load_builtin_scenarios() -> tuple[Scenario, ...]:
    """Возвращает полный кортеж типизированных сценариев системы."""
    scenarios: list[Scenario] = [
        ServiceRedirectScenario(),
        *build_printer_failure_scenarios(),
        CreateUserScenario(),
        build_grant_wlan_scenario(),
        *build_install_printer_scenarios(),
        *build_peripheral_scenarios(),
        PCPerformanceScenario(),
        NetworkDiagnosticsScenario(),
        OSReinstallationScenario(),
        OfflineHostScenario(),
        FileLockScenario(),
        HardwareRepairScenario(),
        RAGConsultationScenario(),
        ConsultationScenario(),
    ]
    return tuple(scenarios)
