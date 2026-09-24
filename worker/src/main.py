"""Worker runtime entrypoint."""

import asyncio
import logging
import signal

from taskiq.receiver import Receiver

import worker.src.tasks.command_dispatcher  # noqa: F401
import worker.src.tasks.sync_kb  # noqa: F401
from worker.src.broker import broker

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

    receiver = Receiver(broker=broker)
    await receiver.listen(finish_event)
    logger.info("IntraLink v2 Worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(run_worker())
