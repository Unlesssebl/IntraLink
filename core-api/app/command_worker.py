"""Standalone process for transactional command delivery and recovery."""

import asyncio
import logging

from app.services.backend_command_consumer import run_backend_consumer
from app.services.command_outbox import run_forever

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [COMMAND-WORKER] - %(levelname)s - %(message)s",
)


if __name__ == "__main__":
    async def main() -> None:
        await asyncio.gather(run_forever(), run_backend_consumer())

    asyncio.run(main())
