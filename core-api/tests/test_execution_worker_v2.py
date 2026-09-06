import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


WORKER_DIR = Path("/execution-worker")
if not WORKER_DIR.exists():
    WORKER_DIR = Path(__file__).resolve().parent.parent.parent / "execution-worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))
spec = importlib.util.spec_from_file_location("windows_execution_worker", WORKER_DIR / "worker.py")
assert spec and spec.loader
worker_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker_module)

from executors.base import ActionResult


@pytest.mark.asyncio
async def test_message_is_not_acked_when_result_persistence_fails():
    worker = worker_module.WindowsExecutionWorker()
    worker.redis = AsyncMock()
    worker._concurrency_sem = asyncio.Semaphore(1)
    worker._process_job = AsyncMock(return_value=False)

    await worker._process_job_safe("stream", "1-0", {"job_id": "job-1"})

    worker.redis.xack.assert_not_awaited()
    await worker.api_client.close()


@pytest.mark.asyncio
async def test_message_is_acked_after_persisted_result():
    worker = worker_module.WindowsExecutionWorker()
    worker.redis = AsyncMock()
    worker._concurrency_sem = asyncio.Semaphore(1)
    worker._process_job = AsyncMock(return_value=True)

    await worker._process_job_safe("stream", "2-0", {"job_id": "job-2"})

    worker.redis.xack.assert_awaited_once_with(
        "stream", worker_module.STREAM_GROUP_NAME, "2-0"
    )
    await worker.api_client.close()


@pytest.mark.asyncio
async def test_diagnose_host_has_a_real_worker_handler():
    worker = worker_module.WindowsExecutionWorker()
    worker.redis = AsyncMock()
    worker.redis.get.return_value = None
    diagnostics = {"target": "PC-001", "is_online": True, "smb_port_445": True}

    with patch.object(
        worker_module, "run_host_diagnostics", new=AsyncMock(return_value=diagnostics)
    ):
        ack = await worker._process_job(
            worker_module.STREAM_EXECUTION_QUEUE,
            "3-0",
            {
                "job_id": "job-diagnostic",
                "action": "diagnose_host",
                "task_id": "0",
                "payload": '{"host":"PC-001"}',
                "mode": "auto",
                "auto_close": "false",
            },
        )

    assert ack is True
    cached_payload = worker.redis.set.await_args_list[-1].args[1]
    assert '"is_online": true' in cached_payload
    await worker.api_client.close()


@pytest.mark.asyncio
async def test_verified_printer_failure_is_persisted_as_structured_result():
    worker = worker_module.WindowsExecutionWorker()
    worker.redis = AsyncMock()
    worker.api_client.claim_command_v2 = AsyncMock(
        return_value=(
            200,
            {
                "claim_token": "claim-1",
                "action": "install_printer",
                "task_id": 42,
                "target": {"pc_name": "PC-001"},
                "parameters": {"printer_name": "Printer-1", "printer_ip": "10.0.0.5"},
            },
        )
    )
    worker.api_client.finish_command_v2 = AsyncMock(return_value=True)
    worker._precheck_host_tcp = AsyncMock(return_value=True)
    worker.printer_exec.install_printer = AsyncMock(
        return_value=ActionResult(
            success=False,
            message="Принтер отсутствует после установки",
            failure_kind="verified_failure",
            failure_code="printer_not_found_after_install",
            verified_failure=True,
        )
    )

    ack = await worker._process_job(
        worker_module.STREAM_EXECUTION_QUEUE_V2,
        "4-0",
        {"command_id": "00000000-0000-0000-0000-000000000042"},
    )

    assert ack is True
    finish_call = worker.api_client.finish_command_v2.await_args
    assert finish_call.args[3] == "failed"
    result = finish_call.kwargs["result"]
    assert result["verified_failure"] is True
    assert result["failure_kind"] == "verified_failure"
    assert result["failure_code"] == "printer_not_found_after_install"
    await worker.api_client.close()


@pytest.mark.asyncio
async def test_printer_verification_distinguishes_absence_from_unavailable_check():
    executor = worker_module.PrinterExecutor()
    executor.run_remote_powershell = AsyncMock(
        side_effect=[
            {"success": True, "data": None},
            {"success": True, "data": ["Other printer"]},
        ]
    )
    absent = await executor.verify("PC-001", [], printer_name="Printer-1")
    assert absent[0] is False
    assert absent[2] is True
    assert absent[3] == "printer_not_found_after_install"

    executor.run_remote_powershell = AsyncMock(
        side_effect=[
            {"success": False, "error": "WinRM timeout"},
            {"success": False, "error": "WinRM timeout"},
        ]
    )
    unavailable = await executor.verify("PC-001", [], printer_name="Printer-1")
    assert unavailable[0] is False
    assert unavailable[2] is False
    assert unavailable[3] == "printer_verification_unavailable"


@pytest.mark.asyncio
async def test_running_claim_conflict_is_not_acked_until_result_is_resolved():
    worker = worker_module.WindowsExecutionWorker()
    worker.api_client.claim_command_v2 = AsyncMock(
        return_value=(
            409,
            {"detail": {"command_status": "running", "reason": "claim_lease_active"}},
        )
    )

    ack = await worker._process_job(
        worker_module.STREAM_EXECUTION_QUEUE_V2,
        "5-0",
        {"command_id": "00000000-0000-0000-0000-000000000043"},
    )

    assert ack is False
    await worker.api_client.close()


@pytest.mark.asyncio
async def test_terminal_claim_conflict_is_safe_to_ack():
    worker = worker_module.WindowsExecutionWorker()
    worker.api_client.claim_command_v2 = AsyncMock(
        return_value=(
            409,
            {"detail": {"command_status": "needs_review", "reason": "lease_expired"}},
        )
    )

    ack = await worker._process_job(
        worker_module.STREAM_EXECUTION_QUEUE_V2,
        "6-0",
        {"command_id": "00000000-0000-0000-0000-000000000044"},
    )

    assert ack is True
    await worker.api_client.close()
