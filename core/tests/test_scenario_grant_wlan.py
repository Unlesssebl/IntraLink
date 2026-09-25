"""Verified LDAP membership tests for GrantWLANScenario."""

from unittest.mock import MagicMock

import pytest

from core.autopilot.dto import AutopilotPolicyDTO
from core.intraservice.dto import ExtractedEntitiesDTO, TaskDTO
from core.scenarios.adapters.grant_wlan import GrantWLANScenario

POLICY = AutopilotPolicyDTO(scenario_key="grant_wlan", mode="ASSISTED")
USER_DN = "CN=Иванов Иван,OU=Users,DC=corporate,DC=loc"
GROUP_DN = "CN=WLAN-WORKNET-ALLOW,OU=Groups,DC=corporate,DC=loc"


def _entry(**attrs):
    entry = MagicMock()
    for name, value in attrs.items():
        attribute = MagicMock()
        if name == "member":
            attribute.values = value
        else:
            attribute.value = value
        setattr(entry, name, attribute)
    return entry


def _scenario_with_searches(search_results):
    pool = MagicMock()
    pool.config.domain = "corporate.loc"
    conn = MagicMock()
    results = iter(search_results)

    def search_side_effect(*args, **kwargs):
        conn.entries = next(results)

    conn.search.side_effect = search_side_effect
    conn.result = {"result": 0, "description": "success"}
    pool.connection_scope.return_value.__enter__.return_value = conn
    return GrantWLANScenario(ad_pool=pool), conn


@pytest.mark.asyncio
async def test_wlan_adds_member_and_verifies_group():
    user = _entry(sAMAccountName="ivanov.i", distinguishedName=USER_DN)
    group_before = _entry(distinguishedName=GROUP_DN, member=[])
    group_after = _entry(member=[USER_DN])
    scenario, conn = _scenario_with_searches([[user], [group_before], [group_after]])

    result = await scenario.execute(
        TaskDTO(Id=1, Entities=ExtractedEntitiesDTO(target_user="ivanov.i")), POLICY
    )

    assert result.success is True
    assert result.metadata["membership_verified"] is True
    assert result.metadata["already_member"] is False
    conn.modify.assert_called_once()


@pytest.mark.asyncio
async def test_wlan_existing_membership_is_idempotent_success():
    user = _entry(sAMAccountName="ivanov.i", distinguishedName=USER_DN)
    group = _entry(distinguishedName=GROUP_DN, member=[USER_DN])
    verified = _entry(member=[USER_DN])
    scenario, conn = _scenario_with_searches([[user], [group], [verified]])

    result = await scenario.execute(
        TaskDTO(Id=2, Entities=ExtractedEntitiesDTO(target_user="ivanov.i")), POLICY
    )

    assert result.success is True
    assert result.metadata["already_member"] is True
    conn.modify.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("matches", [0, 2])
async def test_wlan_rejects_unknown_or_ambiguous_user(matches):
    users = [_entry(sAMAccountName=f"user{index}", distinguishedName=f"CN={index}") for index in range(matches)]
    scenario, conn = _scenario_with_searches([users])

    result = await scenario.execute(
        TaskDTO(Id=3, Entities=ExtractedEntitiesDTO(target_user="user*")), POLICY
    )

    assert result.success is False
    assert result.target_status_id == 2
    conn.modify.assert_not_called()


@pytest.mark.asyncio
async def test_wlan_ldap_modify_failure_does_not_report_success():
    user = _entry(sAMAccountName="ivanov.i", distinguishedName=USER_DN)
    group = _entry(distinguishedName=GROUP_DN, member=[])
    scenario, conn = _scenario_with_searches([[user], [group]])

    def fail_modify(*args, **kwargs):
        conn.result = {"result": 50, "description": "insufficientAccessRights"}

    conn.modify.side_effect = fail_modify
    result = await scenario.execute(
        TaskDTO(Id=4, Entities=ExtractedEntitiesDTO(target_user="ivanov.i")), POLICY
    )

    assert result.success is False
    assert result.target_status_id == 2
    assert "insufficientAccessRights" in (result.error or "")
