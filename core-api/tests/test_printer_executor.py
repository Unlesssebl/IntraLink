import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Добавляем execution-worker в path для тестирования
worker_path = str(Path(__file__).resolve().parent.parent.parent / "execution-worker")
if worker_path not in sys.path:
    sys.path.insert(0, worker_path)

from executors.base import ActionResult, BaseActionExecutor
from executors.printers import PrinterExecutor


@pytest.mark.asyncio
async def test_base_action_executor_host_lock():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock(return_value=1)

    executor = PrinterExecutor(redis_client=mock_redis)
    token = await executor.acquire_host_lock("NTEMW0144", ttl=30)
    assert token is not None
    assert token.startswith("worker_")
    assert mock_redis.set.called

    await executor.release_host_lock("NTEMW0144", token)
    assert mock_redis.eval.called


@pytest.mark.asyncio
async def test_base_action_executor_host_lock_conflict():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=False)  # Замок занят

    executor = PrinterExecutor(redis_client=mock_redis)
    res = await executor.install_printer("NTEMW0144", "HP LaserJet Pro")
    assert res.success is False
    assert "занята другой операцией" in res.message
    assert "HostConcurrencyLockError" in (res.error or "")


@pytest.mark.asyncio
async def test_printer_executor_install_success():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock(return_value=1)

    executor = PrinterExecutor(redis_client=mock_redis)

    # Имитируем успешный preflight, execute и verify
    with patch.object(
        executor, "bootstrap_winrm", new_callable=AsyncMock, return_value=True
    ), patch.object(
        executor,
        "run_remote_powershell",
        new_callable=AsyncMock,
        side_effect=[
            {"success": True, "data": "Installed"},  # Execute
            {"success": True, "data": "HP LaserJet M402dne"},  # Verify
        ],
    ):
        res = await executor.install_printer(
            target_pc="WS-TEST01",
            printer_name="HP LaserJet M402dne",
            printer_ip="10.244.1.200",
        )

        assert res.success is True
        assert "успешно установлен" in res.message
        assert len(res.log) >= 4
        assert any("Preflight" in line for line in res.log)
        assert any("Execute" in line for line in res.log)
        assert any("Verify" in line for line in res.log)
