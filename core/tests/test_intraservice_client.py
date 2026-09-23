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


@pytest.mark.asyncio
async def test_get_task_unwraps_task_form():
    """Verify get_task correctly unwraps Task and included blocks from IntraService."""
    from unittest.mock import AsyncMock, patch

    client = IntraServiceClient(base_url="https://servicedesk.local/api")
    raw_payload = {
        "Task": {
            "Id": 141914,
            "Name": "Сбой Directum",
            "Description": "Ошибка проводника",
        },
        "Statuses": [{"Id": 2, "Name": "В работе"}],
        "Priorities": [{"Id": 1, "Name": "Высокий"}],
        "Attachments": [{"Id": 999, "Name": "error.png", "Size": 1024}],
    }

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = raw_payload
        task = await client.get_task(task_id=141914)
        assert task.id == 141914
        assert task.name == "Сбой Directum"
        assert task.status_name == "В работе"
        assert task.priority_name == "Высокий"
        assert len(task.attachments) == 1
        assert task.attachments[0].name == "error.png"


@pytest.mark.asyncio
async def test_get_task_lifetime_maps_date_and_editor():
    """Verify get_task_lifetime handles real IntraService lifetime structures."""
    from unittest.mock import AsyncMock, patch

    client = IntraServiceClient(base_url="https://servicedesk.local/api")
    raw_lifetime = {
        "TaskLifetimes": [
            {
                "Date": "2026-09-23T16:55:00",
                "EditorId": 43,
                "Editor": "Иванов И.И.",
                "StatusId": 31,
                "Comment": "Работы начаты",
            }
        ],
        "Statuses": [{"Id": 31, "Name": "Открыта"}],
    }

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = raw_lifetime
        events = await client.get_task_lifetime(task_id=141914)
        assert len(events) == 1
        assert events[0].task_id == 141914
        assert events[0].user_name == "Иванов И.И."
        assert events[0].created == "2026-09-23T16:55:00"
        assert events[0].new_status_name == "Открыта"
        assert events[0].comment == "Работы начаты"
