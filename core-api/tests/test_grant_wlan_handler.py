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


@pytest.mark.asyncio
async def test_previously_member_but_removed_before_verify_fails():
    """Пользователь ранее состоял (already_member=True), но на момент verify удален из группы."""
    executor = AsyncMock()
    executor.target_wlan_group = "WLAN-WORKNET"
    status_preflight = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="ivanov.i",
        display_name="Иванов Иван",
        groups=["Domain Users", "WLAN-WORKNET"],
    )
    status_verify_empty = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="ivanov.i",
        display_name="Иванов Иван",
        groups=["Domain Users"],
    )
    executor.get_user_status_async.side_effect = [status_preflight, status_verify_empty]
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
        HandlerContext(command_id="cmd-reg-1"), {"identity": "ivanov.i"}
    )
    assert result.success is False
    assert result.failure_kind == "verification_failed"
    assert result.failure_code == "grant_wlan_verification_failed"
    assert result.verified_failure is False


@pytest.mark.asyncio
async def test_similar_group_name_rejected_in_verification():
    """Пользователь состоит в похожей группе WLAN-WORKNET-TEST, но не в целевой WLAN-WORKNET."""
    executor = AsyncMock()
    executor.target_wlan_group = "WLAN-WORKNET"
    status_preflight = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="sidorov.s",
        display_name="Сидоров Сидор",
        groups=["Domain Users"],
    )
    # Verify returns similar group name (substring match attempt)
    status_verify = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="sidorov.s",
        display_name="Сидоров Сидор",
        groups=["Domain Users", "WLAN-WORKNET-TEST", "OLD-WLAN-WORKNET"],
    )
    executor.get_user_status_async.side_effect = [status_preflight, status_verify]
    executor.grant_wlan_access_async.return_value = ADExecutionResult(
        success=True,
        already_member=False,
        sam_account_name="sidorov.s",
        display_name="Сидоров Сидор",
        message="Добавлен в группу",
        target_group="WLAN-WORKNET",
    )
    handler = GrantWlanHandler(executor)
    result = await handler.run_pipeline(
        HandlerContext(command_id="cmd-reg-2"), {"identity": "sidorov.s"}
    )
    assert result.success is False
    assert result.failure_kind == "verification_failed"
    assert result.failure_code == "grant_wlan_verification_failed"


@pytest.mark.asyncio
async def test_ad_read_failure_during_verify_yields_unverified():
    """Сбой чтения AD при verify не считает действие успехом и возвращает ad_read_unavailable."""
    executor = AsyncMock()
    executor.target_wlan_group = "WLAN-WORKNET"
    status_preflight = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="kuznetsov.k",
        display_name="Кузнецов К",
        groups=["Domain Users"],
    )
    status_verify_unavailable = ADUserStatus(
        found=False,
        lookup_state="unavailable",
        error="RPC server unavailable (0x800706BA)",
    )
    executor.get_user_status_async.side_effect = [status_preflight, status_verify_unavailable]
    executor.grant_wlan_access_async.return_value = ADExecutionResult(
        success=True,
        already_member=False,
        sam_account_name="kuznetsov.k",
        message="Добавлен",
        target_group="WLAN-WORKNET",
    )
    handler = GrantWlanHandler(executor)
    result = await handler.run_pipeline(
        HandlerContext(command_id="cmd-reg-3"), {"identity": "kuznetsov.k"}
    )
    assert result.success is False
    assert result.failure_kind == "verification_failed"
    assert result.failure_code == "ad_read_unavailable"
    assert result.verified_failure is False


@pytest.mark.asyncio
async def test_grant_wlan_reconcile_idempotency():
    """Метод reconcile подтверждает членство без вызова мутирующих команд."""
    executor = AsyncMock()
    executor.target_wlan_group = "WLAN-WORKNET"
    executor.get_user_status_async.return_value = ADUserStatus(
        found=True,
        enabled=True,
        sam_account_name="ivanov.i",
        groups=["Domain Users", "WLAN-WORKNET"],
    )
    handler = GrantWlanHandler(executor)
    satisfied, reason = await handler.reconcile(
        HandlerContext(command_id="cmd-reg-4"),
        GrantWlanInput(identity="ivanov.i"),
    )
    assert satisfied is True
    assert "уже состоит" in reason
    executor.grant_wlan_access_async.assert_not_awaited()
    executor.add_user_to_group_async.assert_not_awaited()
