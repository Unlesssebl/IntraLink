import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

WORKER_DIR = Path(__file__).resolve().parent.parent.parent / "execution-worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from executors.ad import ADExecutionResult, ADUserStatus  # noqa: E402
from handlers.grant_wlan import GrantWlanHandler, GrantWlanInput  # noqa: E402
from sdk.models import HandlerContext, RiskClass  # noqa: E402


@pytest.mark.asyncio
async def test_invalid_identity_rejected_in_validation():
    executor = AsyncMock()
    handler = GrantWlanHandler(executor)

    # Injection character rejected
    result = await handler.run_pipeline(
        HandlerContext(command_id="cmd-1"), {"identity": "user; rm -rf /"}
    )
    assert result.success is False
    assert result.failure_kind == "validation_error"
    executor.get_user_status_async.assert_not_awaited()
    executor.grant_wlan_access_async.assert_not_awaited()
    assert handler.risk_class is RiskClass.NEVER_AUTO_RETRY


@pytest.mark.asyncio
async def test_user_not_found_stops_in_preflight():
    executor = AsyncMock()
    executor.get_user_status_async.return_value = ADUserStatus(
        found=False,
        enabled=False,
        sam_account_name=None,
    )
    handler = GrantWlanHandler(executor)
    result = await handler.run_pipeline(
        HandlerContext(command_id="cmd-2"), {"identity": "unknown.user"}
    )
    assert result.success is False
    assert result.failure_kind == "preflight_failed"
    assert result.failure_code == "user_not_found"
    executor.grant_wlan_access_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_user_stops_in_preflight():
    executor = AsyncMock()
    executor.get_user_status_async.return_value = ADUserStatus(
        found=True,
        enabled=False,
        sam_account_name="disabled.user",
        display_name="Отключенный Пользователь",
    )
    handler = GrantWlanHandler(executor)
    result = await handler.run_pipeline(
        HandlerContext(command_id="cmd-3"), {"identity": "disabled.user"}
    )
    assert result.success is False
    assert result.failure_kind == "preflight_failed"
    assert result.failure_code == "user_account_disabled"
    executor.grant_wlan_access_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_already_member_idempotent_success():
    executor = AsyncMock()
    executor.target_wlan_group = "WLAN-WORKNET"
    # Preflight status
    executor.get_user_status_async.return_value = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="ivanov.i",
        display_name="Иванов Иван",
        groups=["Domain Users", "WLAN-WORKNET"],
    )
    # Execute returns already_member
    executor.grant_wlan_access_async.return_value = ADExecutionResult(
        success=True,
        already_member=True,
        sam_account_name="ivanov.i",
        display_name="Иванов Иван",
        message="Пользователь уже состоит в группе",
        target_group="WLAN-WORKNET",
    )
    handler = GrantWlanHandler(executor)
    result = await handler.run_pipeline(
        HandlerContext(command_id="cmd-4"), {"identity": "ivanov.i"}
    )
    assert result.success is True
    assert result.payload["verified"] is True
    assert result.payload["already_member"] is True


@pytest.mark.asyncio
async def test_add_to_group_and_verify_success():
    executor = AsyncMock()
    executor.target_wlan_group = "WLAN-WORKNET"
    # In preflight user is not yet in group
    status_before = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="petrov.p",
        display_name="Петров Петр",
        groups=["Domain Users"],
    )
    # In verify user is in group
    status_after = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="petrov.p",
        display_name="Петров Петр",
        groups=["Domain Users", "WLAN-WORKNET"],
    )
    executor.get_user_status_async.side_effect = [status_before, status_after]
    executor.grant_wlan_access_async.return_value = ADExecutionResult(
        success=True,
        already_member=False,
        sam_account_name="petrov.p",
        display_name="Петров Петр",
        message="Пользователь успешно добавлен в группу",
        target_group="WLAN-WORKNET",
    )
    handler = GrantWlanHandler(executor)
    result = await handler.run_pipeline(
        HandlerContext(command_id="cmd-5"), {"identity": "petrov.p"}
    )
    assert result.success is True
    assert result.payload["verified"] is True
    assert result.payload["already_member"] is False
    assert result.payload["sam_account_name"] == "petrov.p"
