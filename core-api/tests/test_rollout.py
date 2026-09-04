from unittest.mock import AsyncMock, patch

import pytest

from app.services.rollout import rollout_readiness


@pytest.mark.asyncio
async def test_rollout_check_requires_matching_sha_and_audit_table():
    with patch("app.services.rollout.build_versions", return_value={"api_sha": "abc123", "web_sha": "abc123"}), patch(
        "app.services.rollout.security_audit_table_exists", new_callable=AsyncMock, return_value=True
    ):
        result = await rollout_readiness(expected_sha="abc123")
    assert result["ready"] is True


@pytest.mark.asyncio
async def test_rollout_check_fails_closed_when_audit_table_is_missing():
    with patch("app.services.rollout.build_versions", return_value={"api_sha": "abc123", "web_sha": "abc123"}), patch(
        "app.services.rollout.security_audit_table_exists", new_callable=AsyncMock, return_value=False
    ):
        result = await rollout_readiness(expected_sha="abc123")
    assert result["ready"] is False
    assert "security_audit_log" in result["checks"][0]
