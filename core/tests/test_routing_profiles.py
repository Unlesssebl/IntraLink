"""Unit tests for ScenarioRoutingProfile and RoutingProfileRegistry."""

import pytest

from core.routing.profile_registry import get_default_profile_registry, reset_profile_registry
from core.routing.profiles import (
    DEFAULT_PROFILES,
    ScenarioRoutingProfile,
)
from core.scenarios.registry import get_default_scenario_registry


@pytest.fixture(autouse=True)
def clean_registry():
    reset_profile_registry()
    yield
    reset_profile_registry()


def test_profile_keys_match_default_scenario_registry():
    """DoD: Profile keys in profile registry must strictly match default ScenarioRegistry."""
    scenario_registry = get_default_scenario_registry()
    scenario_keys = {s.scenario_key for s in scenario_registry.list_all()}

    profile_registry = get_default_profile_registry()
    profile_keys = {p.scenario_key for p in profile_registry.list_all()}

    assert profile_keys == scenario_keys
    assert len(profile_keys) == 9


def test_profile_versions_non_empty():
    """All scenario routing profiles must declare non-empty semantic versions."""
    for profile in DEFAULT_PROFILES:
        assert profile.scenario_version
        assert isinstance(profile.scenario_version, str)
        assert len(profile.scenario_version.strip()) > 0


def test_service_ids_verified_against_existing_definitions():
    """Declared exact_service_ids must be real IDs confirmed by existing codebase."""
    allowed_known_service_ids = {
        8,    # Account Lock
        12,   # Workplaces & Peripherals / Spooler
        19,   # Printer Support & Maintenance
        55,   # Directum User Onboarding
        62,   # Printer Install
        63,   # WLAN
        71,   # Offline Host / Network
        82,   # Printer Install
        83,   # MFU Install
        112,  # Offline Host / Hardware
        183,  # Printer Install
        232,  # Directum Access Provisioning
        999,  # Non-target redirect
    }

    for profile in DEFAULT_PROFILES:
        for sid in profile.exact_service_ids:
            assert sid in allowed_known_service_ids, f"Unknown/invented service_id {sid} in profile {profile.scenario_key}"


def test_rag_consultation_is_consultation_only():
    """rag_consultation profile must have consultation_only=True and no exact service IDs."""
    profile_registry = get_default_profile_registry()
    rag_profile = profile_registry.get("rag_consultation")
    assert rag_profile is not None
    assert rag_profile.consultation_only is True
    assert len(rag_profile.exact_service_ids) == 0
    assert len(rag_profile.service_name_terms) == 0


def test_profile_intent_summaries_non_empty():
    """All scenario routing profiles must declare non-empty Russian intent summary."""
    for profile in DEFAULT_PROFILES:
        assert profile.intent_summary
        assert isinstance(profile.intent_summary, str)
        assert len(profile.intent_summary.strip()) > 10


def test_duplicate_scenario_key_registration_forbidden():
    """Registering duplicate scenario_key raises ValueError."""
    profile_registry = get_default_profile_registry()
    dup = ScenarioRoutingProfile(
        scenario_key="install_printer",
        scenario_version="2.0.0",
        intent_summary="Дубликат профиля установки принтера",
    )
    with pytest.raises(ValueError, match="Duplicate scenario_key 'install_printer'"):
        profile_registry.register(dup)


def test_by_service_id_lookup():
    """ServiceId 19 returns multiple printing profiles."""
    profile_registry = get_default_profile_registry()
    profiles_19 = profile_registry.by_service_id(19)
    keys_19 = {p.scenario_key for p in profiles_19}

    assert "install_printer" in keys_19
    assert "printer_spooler_restart" in keys_19
    assert "default_printer_fix" in keys_19
