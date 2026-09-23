"""Worker runtime entrypoint."""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("intralink-worker")


async def run_worker() -> None:
    logger.info("IntraLink v2 Worker initialized.")
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(run_worker())
