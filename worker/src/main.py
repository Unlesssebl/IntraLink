"""Worker runtime entrypoint."""

import asyncio
import logging
import os
import signal
import socket

from taskiq.receiver import Receiver

import worker.src.tasks.command_dispatcher  # noqa: F401
import worker.src.tasks.poller  # noqa: F401
import worker.src.tasks.sync_kb  # noqa: F401
from worker.src.broker import broker
from worker.src.tasks.poller import run_poller_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("intralink-worker")


async def run_worker_heartbeat_loop(stop_event: asyncio.Event) -> None:
    """Publish heartbeat key automation:worker:{node_id} every 10 seconds."""
    from core.automation.capabilities import get_default_capability_registry
    from core.automation.execution_policy import is_deployment_force_disabled
    from core.automation.worker_attestation import (
        WORKER_HEARTBEAT_INTERVAL_SECONDS,
        WorkerAttestationService,
    )
    from core.redis_client import get_redis_client

    node_id = os.getenv("WORKER_NODE_ID", f"worker-{socket.gethostname()}-{os.getpid()}")
    registry = get_default_capability_registry()
    capabilities = [spec.key for spec in registry.list_all() if registry.get_executor(spec.key) is not None]
    executor_types = sorted({spec.executor for spec in registry.list_all() if registry.get_executor(spec.key) is not None})
    attestation_svc = WorkerAttestationService()

    logger.info("Worker attestation heartbeat starting for node %s (capabilities: %s)...", node_id, capabilities)
    try:
        redis_client = get_redis_client()
    except Exception as exc:
        logger.error("Failed to initialize Redis client for worker heartbeat: %s", exc)
        return

    while not stop_event.is_set():
        try:
            force_disabled = is_deployment_force_disabled()
            await attestation_svc.publish_heartbeat(
                redis_client,
                node_id=node_id,
                capabilities=capabilities,
                executor_types=executor_types,
                force_disabled=force_disabled,
            )
        except Exception as exc:
            logger.warning("Failed to publish worker heartbeat: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=WORKER_HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

    try:
        await redis_client.delete(attestation_svc.heartbeat_key(node_id))
    except Exception:
        pass
    logger.info("Worker attestation heartbeat stopped for node %s", node_id)


async def run_worker() -> None:
    logger.info("IntraLink v2 Worker starting with Taskiq runtime...")
    finish_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, finish_event.set)
        except NotImplementedError:
            # Windows signal handler fallback
            pass

    enable_poller = os.getenv("ENABLE_POLLER", "true").lower() in ("true", "1", "yes")
    poller_coro = None
    if enable_poller:
        logger.info("Starting background ingestion poller loop (30s pulse)...")
        poller_coro = asyncio.create_task(run_poller_loop(stop_event=finish_event))

    heartbeat_coro = asyncio.create_task(run_worker_heartbeat_loop(stop_event=finish_event))

    receiver = Receiver(broker=broker)
    try:
        await receiver.listen(finish_event)
    finally:
        finish_event.set()
        if poller_coro is not None:
            await poller_coro
        await heartbeat_coro
    logger.info("IntraLink v2 Worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(run_worker())
