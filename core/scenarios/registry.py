"""Scenario registry and discovery service for IntraLink v2."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from openai import AsyncOpenAI

from core.intraservice.dto import TaskDTO
from core.scenarios.adapters.grant_wlan import GrantWLANScenario
from core.scenarios.adapters.install_printer import InstallPrinterScenario
from core.scenarios.adapters.offline_host import OfflineHostScenario
from core.scenarios.adapters.rag_consultation import RAGConsultationScenario
from core.scenarios.adapters.service_redirect import ServiceRedirectScenario
from core.scenarios.base import BaseScenario
from core.scenarios.router import ScenarioRouter

logger = logging.getLogger("core.scenarios.registry")


class ScenarioRegistry:
    """Registry maintaining active autopilot scenarios with multi-factor routing."""

    def __init__(self, router: Optional[ScenarioRouter] = None) -> None:
        self._scenarios: Dict[str, BaseScenario] = {}
        self._scenarios_by_service_id: Dict[int, List[BaseScenario]] = {}
        self._router = router or ScenarioRouter()

    async def initialize(self) -> None:
        """Warm up the semantic prototype index (non-blocking; safe to call multiple times)."""
        await self._router.warm_up()

    def register(self, scenario: BaseScenario) -> None:
        """Register a scenario instance and index its service IDs."""
        self._scenarios[scenario.scenario_key] = scenario
        if getattr(scenario, "definition", None) and scenario.definition.service_ids:
            for sid in scenario.definition.service_ids:
                self._scenarios_by_service_id.setdefault(sid, []).append(scenario)
        logger.debug("Registered scenario '%s' (%s)", scenario.scenario_key, scenario.name)

    def get(self, scenario_key: str) -> Optional[BaseScenario]:
        """Get scenario by key."""
        return self._scenarios.get(scenario_key)

    def get_scenario(self, scenario_key: str) -> Optional[BaseScenario]:
        """Alias for get(scenario_key)."""
        return self.get(scenario_key)

    def get_by_service_id(self, service_id: int) -> List[BaseScenario]:
        """Get scenarios matching catalog service ID."""
        return self._scenarios_by_service_id.get(service_id, [])

    def list_all(self) -> List[BaseScenario]:
        """List all registered scenarios."""
        return list(self._scenarios.values())

    async def find_scenario(self, task: TaskDTO) -> Optional[BaseScenario]:
        """Find matching scenario using multi-factor Bayesian-like routing (A+B+C+D+E)."""
        routed = await self._router.route_task(task, self._scenarios)
        if routed:
            scenario, match = routed
            logger.info(
                "Registry: router matched '%s' (conf: %.3f) for ticket #%s",
                scenario.scenario_key,
                match.confidence,
                task.id,
            )
            return scenario
        return None


_global_registry: Optional[ScenarioRegistry] = None


def get_default_scenario_registry(ai_client: Optional[AsyncOpenAI] = None) -> ScenarioRegistry:
    """Singleton factory for ScenarioRegistry with active Core scenarios pre-registered."""
    global _global_registry
    if _global_registry is None:
        router = ScenarioRouter(ai_client=ai_client)
        registry = ScenarioRegistry(router=router)
        registry.register(InstallPrinterScenario())
        registry.register(GrantWLANScenario())
        registry.register(ServiceRedirectScenario())
        registry.register(OfflineHostScenario())
        registry.register(RAGConsultationScenario())
        _global_registry = registry
    return _global_registry


def reset_registry() -> None:
    """Reset singleton (for testing purposes only)."""
    global _global_registry
    _global_registry = None
