import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


WORKER_DIR = Path("/execution-worker")
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))
spec = importlib.util.spec_from_file_location("windows_execution_worker", WORKER_DIR / "worker.py")
assert spec and spec.loader
worker_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker_module)


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
