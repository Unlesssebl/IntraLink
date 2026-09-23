"""Scenario: WLAN-WORKNET access grant (grant_wlan)."""

from __future__ import annotations

from app.services.scenarios.definitions.base_definition import FactActionScenario, contains_any
from shared.domain import FactRequirement, ScenarioDefinition


def build_grant_wlan_scenario() -> FactActionScenario:
    return FactActionScenario(
        ScenarioDefinition(
            key="grant_wlan",
            version=1,
            risk_level=2,
            allowed_actions=["grant_wlan"],
            clarification_outcome_key="account_details_invalid",
            success_outcome_key="resolved_standard",
            required_facts=[
                FactRequirement(key="identity", clarification_key="clarify_identity")
            ],
        ),
        contains_any("wlan", "wi-fi", "wifi", "вайф"),
        action="grant_wlan",
        outcome_key="grant_wlan_proposed",
        parameter_map={"identity": "identity"},
    )
