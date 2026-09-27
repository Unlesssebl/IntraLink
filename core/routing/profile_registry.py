"""Scenario routing profile registry for Evidence-Based Routing Cascade.

Maintains verified scenario profiles, indexed by scenario_key and exact service_ids.
Enforces uniqueness of scenario keys during registration.
"""

from typing import Dict, List, Optional, Sequence

from core.routing.profiles import DEFAULT_PROFILES, ScenarioRoutingProfile


class RoutingProfileRegistry:
    """Registry maintaining active ScenarioRoutingProfile instances."""

    def __init__(self, profiles: Optional[Sequence[ScenarioRoutingProfile]] = None) -> None:
        self._profiles_by_key: Dict[str, ScenarioRoutingProfile] = {}
        self._profiles_by_service_id: Dict[int, List[ScenarioRoutingProfile]] = {}

        if profiles:
            for profile in profiles:
                self.register(profile)

    def register(self, profile: ScenarioRoutingProfile) -> None:
        """Register a scenario profile and index by service_id.

        Raises:
            ValueError: If a profile with the same scenario_key is already registered.
        """
        if profile.scenario_key in self._profiles_by_key:
            raise ValueError(
                f"Duplicate scenario_key '{profile.scenario_key}' cannot be registered in RoutingProfileRegistry."
            )

        self._profiles_by_key[profile.scenario_key] = profile

        for sid in profile.exact_service_ids:
            self._profiles_by_service_id.setdefault(sid, []).append(profile)

    def get(self, key: str) -> Optional[ScenarioRoutingProfile]:
        """Retrieve profile by its unique scenario key."""
        return self._profiles_by_key.get(key)

    def list_all(self) -> List[ScenarioRoutingProfile]:
        """Return list of all registered scenario profiles."""
        return list(self._profiles_by_key.values())

    def by_service_id(self, service_id: int) -> List[ScenarioRoutingProfile]:
        """Return all scenario profiles associated with the exact service_id."""
        return list(self._profiles_by_service_id.get(service_id, []))


_default_profile_registry: Optional[RoutingProfileRegistry] = None


def get_default_profile_registry() -> RoutingProfileRegistry:
    """Return default singleton RoutingProfileRegistry populated with canonical profiles."""
    global _default_profile_registry
    if _default_profile_registry is None:
        _default_profile_registry = RoutingProfileRegistry(profiles=DEFAULT_PROFILES)
    return _default_profile_registry


def reset_profile_registry() -> None:
    """Reset default profile registry singleton (for testing only)."""
    global _default_profile_registry
    _default_profile_registry = None
