"""Worker runtime entrypoint."""

import asyncio
import logging
import os
import signal

from taskiq.receiver import Receiver

import worker.src.tasks.autopilot  # noqa: F401
import worker.src.tasks.command_dispatcher  # noqa: F401
import worker.src.tasks.poller  # noqa: F401
import worker.src.tasks.sync_kb  # noqa: F401
import worker.src.tasks.triage  # noqa: F401
from worker.src.broker import broker
from worker.src.scenarios.registry import get_default_scenario_registry
from worker.src.tasks.poller import run_poller_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("intralink-worker")


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

    # Warm up the semantic scenario routing index (vectorises prototypes via BGE-M3 once)
    logger.info("Warming up scenario semantic index (RAG prototype embeddings)...")
    try:
        await get_default_scenario_registry().initialize()
    except Exception as exc:
        logger.warning("Semantic index warm-up failed (Factor E disabled for this session): %s", exc)

    receiver = Receiver(broker=broker)
    try:
        await receiver.listen(finish_event)
    finally:
        finish_event.set()
        if poller_coro is not None:
            await poller_coro
    logger.info("IntraLink v2 Worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(run_worker())
