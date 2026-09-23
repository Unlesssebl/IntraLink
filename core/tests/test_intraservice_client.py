"""Test IntraService client URL normalization and Circuit Breaker logic."""

import pytest

from core.intraservice.client import CircuitBreaker, CircuitState, IntraServiceClient
from core.intraservice.exceptions import CircuitBreakerOpenError


def test_base_url_normalization_no_duplicate_api():
    """Verify GEMINI.md rule: base URL must always cleanly end with /api without duplication."""
    c1 = IntraServiceClient(base_url="https://servicedesk-pub.corporate.loc/api")
    assert c1.base_url == "https://servicedesk-pub.corporate.loc/api"

    c2 = IntraServiceClient(base_url="https://servicedesk-pub.corporate.loc/api/")
    assert c2.base_url == "https://servicedesk-pub.corporate.loc/api"

    c3 = IntraServiceClient(base_url="https://servicedesk-pub.corporate.loc")
    assert c3.base_url == "https://servicedesk-pub.corporate.loc/api"


def test_circuit_breaker_transition_and_trip():
    cb = CircuitBreaker(failure_threshold=3, base_recovery_timeout=1.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1st failure
    cb.record_failure("Error 1")
    assert cb.state == CircuitState.CLOSED

    # 2nd failure
    cb.record_failure("Error 2")
    assert cb.state == CircuitState.CLOSED

    # 3rd failure -> TRIPPED to OPEN
    cb.record_failure("Error 3")
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False
    assert cb.remaining_cooldown() > 0


@pytest.mark.asyncio
async def test_client_blocks_when_circuit_breaker_open():
    client = IntraServiceClient(base_url="https://servicedesk.local/api")
    client.circuit_breaker.state = CircuitState.OPEN
    client.circuit_breaker.last_state_change = 9999999999.0  # far future -> open

    with pytest.raises(CircuitBreakerOpenError):
        await client._request("GET", "task/123")
