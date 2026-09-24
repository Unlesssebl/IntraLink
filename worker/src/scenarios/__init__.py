"""Worker Scenarios Package (Core-6 and Scenario Registry)."""

from .ad_password_reset import ADPasswordResetScenario
from .base import BaseScenario, PreconditionResult, ScenarioExecutionResult
from .grant_wlan import GrantWLANScenario
from .install_printer import InstallPrinterScenario
from .offline_host import OfflineHostScenario
from .rag_consultation import RAGConsultationScenario
from .registry import ScenarioRegistry, get_default_scenario_registry, reset_registry
from .router import ScenarioRouter
from .semantic_index import SemanticPrototypeIndex
from .service_redirect import ServiceRedirectScenario

__all__ = [
    "BaseScenario",
    "PreconditionResult",
    "ScenarioExecutionResult",
    "InstallPrinterScenario",
    "ADPasswordResetScenario",
    "GrantWLANScenario",
    "OfflineHostScenario",
    "ServiceRedirectScenario",
    "RAGConsultationScenario",
    "ScenarioRegistry",
    "ScenarioRouter",
    "SemanticPrototypeIndex",
    "get_default_scenario_registry",
    "reset_registry",
]
