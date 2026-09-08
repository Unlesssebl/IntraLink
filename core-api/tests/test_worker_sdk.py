"""
Контрактные тесты для Worker SDK (Инкремент 2).
Проверяет:
- 7-фазный жизненный цикл ActionHandler и гарантированный cleanup;
- Двухконтурную безопасность PowerShell Runner (защита от инъекций, передача через stdin);
- Принудительную остановку процесса при отмене через cancellation_token;
- Фоновый LeaseRenewer при получении 409 Conflict или потере блокировки хоста;
- Реестр обработчиков HandlerRegistry.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, Field

# Добавляем execution-worker в sys.path
WORKER_DIR = Path(__file__).resolve().parent.parent.parent / "execution-worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

from sdk.base import ActionHandler
from sdk.lease_renewer import LeaseRenewer
from sdk.models import ActionResult, ExecutionPhase, HandlerContext, RiskClass
from sdk.powershell import run_powershell_safe
from sdk.registry import HandlerRegistry


class DummyInput(BaseModel):
    target_pc: str = Field(..., min_length=1)
    timeout_sec: int = Field(default=30)


class MockLifecycleHandler(ActionHandler[DummyInput]):
    id = "mock_action"
    version = "1.2.0"
    capabilities = ["windows", "winrm"]
    risk_class = RiskClass.NEVER_AUTO_RETRY
    input_model = DummyInput

    def __init__(self) -> None:
        self.executed_phases: list[str] = []
        self.fail_at_phase: ExecutionPhase | None = None
        self.exception_at_phase: ExecutionPhase | None = None

    async def validate(self, ctx: HandlerContext, params: DummyInput) -> tuple[bool, str]:
        self.executed_phases.append("validate")
        if self.fail_at_phase == ExecutionPhase.VALIDATE:
            return False, "Validation rejected"
        return True, "Validation OK"

    async def preflight(
        self, ctx: HandlerContext, params: DummyInput
    ) -> tuple[bool, str, dict[str, Any]]:
        self.executed_phases.append("preflight")
        if self.fail_at_phase == ExecutionPhase.PREFLIGHT:
            return False, "Preflight rejected", {}
        return True, "Preflight OK", {"host_online": True, "port_5985": True}

    async def prepare(self, ctx: HandlerContext, params: DummyInput) -> tuple[bool, str]:
        self.executed_phases.append("prepare")
        if self.fail_at_phase == ExecutionPhase.PREPARE:
            return False, "Prepare failed"
        return True, "Prepare OK"

    async def execute(self, ctx: HandlerContext, params: DummyInput) -> ActionResult:
        self.executed_phases.append("execute")
        if self.exception_at_phase == ExecutionPhase.EXECUTE:
            raise RuntimeError("Explosion in execute phase")
        if self.fail_at_phase == ExecutionPhase.EXECUTE:
            return ActionResult(success=False, message="Execute failed", failure_code="EXEC_ERR")
        return ActionResult(success=True, message="Execute OK", payload={"applied": True})

    async def verify(
        self, ctx: HandlerContext, params: DummyInput, result: ActionResult
    ) -> tuple[bool, str, bool, str | None]:
        self.executed_phases.append("verify")
        if self.fail_at_phase == ExecutionPhase.VERIFY:
            return False, "Verify mismatch", True, "STATE_MISMATCH"
        return True, "Verify OK", False, None

    async def reconcile(self, ctx: HandlerContext, params: DummyInput) -> tuple[bool, str]:
        self.executed_phases.append("reconcile")
        return True, "Reconcile OK"

    async def cleanup(self, ctx: HandlerContext, params: DummyInput) -> None:
        self.executed_phases.append("cleanup")


def _make_ctx() -> HandlerContext:
    return HandlerContext(
        command_id="cmd-test-123",
        task_id=42,
        node_id="node-test-01",
        worker_id="worker-win-01",
    )


# --- 1. Тесты жизненного цикла ---


@pytest.mark.asyncio
async def test_handler_full_lifecycle_success():
    handler = MockLifecycleHandler()
    ctx = _make_ctx()
    raw_params = {"target_pc": "PC-001", "timeout_sec": 10}

    result = await handler.run_pipeline(ctx, raw_params)

    assert result.success is True
    assert result.message == "Verify OK"
    assert result.payload == {"applied": True}
    assert ctx.evidence == {"host_online": True, "port_5985": True}
    assert handler.executed_phases == [
        "validate",
        "preflight",
        "prepare",
        "execute",
        "verify",
        "cleanup",
    ]


@pytest.mark.asyncio
async def test_cleanup_is_guaranteed_on_execute_exception():
    """Фаза cleanup обязана выполниться даже при аварийном исключении в execute."""
    handler = MockLifecycleHandler()
    handler.exception_at_phase = ExecutionPhase.EXECUTE
    ctx = _make_ctx()
    raw_params = {"target_pc": "PC-001"}

    result = await handler.run_pipeline(ctx, raw_params)

    assert result.success is False
    assert result.failure_kind == "unhandled_exception"
    assert "Explosion in execute phase" in (result.error or "")
    assert "cleanup" in handler.executed_phases
    assert handler.executed_phases == ["validate", "preflight", "prepare", "execute", "cleanup"]


@pytest.mark.asyncio
async def test_prepare_failure_aborts_before_execute_and_runs_cleanup():
    """Сбой в prepare останавливает конвейер до execute, но вызывает cleanup."""
    handler = MockLifecycleHandler()
    handler.fail_at_phase = ExecutionPhase.PREPARE
    ctx = _make_ctx()
    raw_params = {"target_pc": "PC-001"}

    result = await handler.run_pipeline(ctx, raw_params)

    assert result.success is False
    assert result.failure_kind == "prepare_failed"
    assert "execute" not in handler.executed_phases
    assert "cleanup" in handler.executed_phases


@pytest.mark.asyncio
async def test_preflight_failure_aborts_before_prepare_and_execute():
    handler = MockLifecycleHandler()
    handler.fail_at_phase = ExecutionPhase.PREFLIGHT
    ctx = _make_ctx()
    raw_params = {"target_pc": "PC-001"}

    result = await handler.run_pipeline(ctx, raw_params)

    assert result.success is False
    assert result.failure_kind == "preflight_failed"
    assert "prepare" not in handler.executed_phases
    assert "execute" not in handler.executed_phases
    assert "cleanup" in handler.executed_phases


@pytest.mark.asyncio
async def test_cancellation_token_stops_pipeline():
    handler = MockLifecycleHandler()
    ctx = _make_ctx()
    ctx.cancellation_token.set()  # Отмена до начала
    raw_params = {"target_pc": "PC-001"}

    result = await handler.run_pipeline(ctx, raw_params)

    assert result.success is False
    assert result.failure_kind == "cancelled"
    assert "execute" not in handler.executed_phases


# --- 2. Тесты безопасности PowerShell Runner ---


@pytest.mark.asyncio
async def test_powershell_safe_runner_prevents_injection_via_stdin_json():
    """
    Проверяет, что зловредные спецсимволы в аргументах передаются строго
    как буквальные строковые данные через stdin и не исполняются командной строкой.
    """
    script = """
    $raw = [Console]::In.ReadToEnd()
    $obj = $raw | ConvertFrom-Json
    [PSCustomObject]@{
        received_target = $obj.target_pc
        received_nested = $obj.malicious_arg
        received_quotes = $obj.quotes_test
    } | ConvertTo-Json -Compress
    """

    payload = {
        "target_pc": "PC-01; Remove-Item C:\\nonexistent -Force",
        "malicious_arg": "$(Write-Output 'Pwned')",
        "quotes_test": 'double" and single\' and `backtick`',
    }

    res = await run_powershell_safe(script, payload=payload)

    assert res.return_code == 0
    assert res.parsed_json is not None
    assert res.parsed_json["received_target"] == "PC-01; Remove-Item C:\\nonexistent -Force"
    assert res.parsed_json["received_nested"] == "$(Write-Output 'Pwned')"
    assert res.parsed_json["received_quotes"] == 'double" and single\' and `backtick`'


@pytest.mark.asyncio
async def test_powershell_runner_cancellation_kills_process():
    """Проверяет принудительное прерывание зависшего PowerShell-процесса при активации cancellation_token."""
    script = """
    Start-Sleep -Seconds 30
    """
    token = asyncio.Event()

    async def _trigger_cancel():
        await asyncio.sleep(0.3)
        token.set()

    cancel_task = asyncio.create_task(_trigger_cancel())

    with pytest.raises(asyncio.CancelledError):
        await run_powershell_safe(script, cancellation_token=token, timeout_seconds=10.0)

    await cancel_task


# --- 3. Тесты LeaseRenewer ---


@pytest.mark.asyncio
async def test_lease_renewer_triggers_cancellation_on_409_conflict():
    """При получении 409 Conflict от Core API renewer немедленно выставляет cancellation_token."""
    mock_api = AsyncMock()
    mock_api.renew_command_lease_v2.return_value = (409, {"reason": "stale_claim"})
    token = asyncio.Event()

    renewer = LeaseRenewer(
        command_id="cmd-1",
        worker_id="w-1",
        claim_token="tok-1",
        cancellation_token=token,
        api_client=mock_api,
        interval_seconds=0.05,
    )

    await renewer.start()
    await asyncio.sleep(0.15)
    await renewer.stop()

    assert token.is_set() is True


@pytest.mark.asyncio
async def test_lease_renewer_triggers_cancellation_on_redis_lock_loss():
    """При потере host lock в Redis renewer немедленно выставляет cancellation_token."""
    mock_api = AsyncMock()
    mock_api.renew_command_lease_v2.return_value = (200, {"status": "running"})

    mock_redis = AsyncMock()
    # eval возвращает 0 (ключ удален или перехвачен другим токеном)
    mock_redis.eval.return_value = 0
    token = asyncio.Event()

    renewer = LeaseRenewer(
        command_id="cmd-1",
        worker_id="w-1",
        claim_token="tok-1",
        cancellation_token=token,
        api_client=mock_api,
        redis=mock_redis,
        host_lock_key="lock:host:PC-01",
        host_lock_token="owner-token-123",
        interval_seconds=0.05,
    )

    await renewer.start()
    await asyncio.sleep(0.15)
    await renewer.stop()

    assert token.is_set() is True


# --- 4. Тесты HandlerRegistry ---


def test_handler_registry():
    registry = HandlerRegistry()
    handler = MockLifecycleHandler()

    assert registry.supports_action("mock_action") is False
    registry.register(handler)

    assert registry.supports_action("mock_action") is True
    assert registry.get("mock_action") is handler
    assert registry.get("unknown_action") is None

    assert registry.supports_capabilities(["winrm"]) is True
    assert registry.supports_capabilities(["winrm", "windows"]) is True
    assert registry.supports_capabilities(["nonexistent_cap"]) is False

    assert "winrm" in registry.list_capabilities()
    assert registry.list_actions() == {"mock_action": "1.2.0"}
