"""Task dispatch gateway: API → Worker boundary via Taskiq broker.

This module is the sole integration point between API and the Worker task queue.
API code MUST use `dispatch_command(command_id)` instead of importing
worker task modules directly. This enforces service isolation:
the API container can operate without mounting the worker/ directory.
"""

import logging
from uuid import UUID

from core.broker import broker

logger = logging.getLogger("api.core.task_dispatch")

DISPATCH_COMMAND_TASK = "dispatch_command_task"

# Client-side task handle for enqueuing via Taskiq without importing worker code
dispatch_command_task = broker.register_task(
    lambda command_id: None,
    task_name=DISPATCH_COMMAND_TASK,
)


async def dispatch_command(command_id: UUID) -> None:
    """Enqueue a CommandRecord for execution by the worker via Taskiq.

    Uses dispatch_command_task.kiq() with registered proxy to avoid importing
    worker task modules (which would create an unwanted code dependency
    and prevent API-only container deployments).

    Args:
        command_id: UUID of the CommandRecord to execute.

    Raises:
        Does NOT raise — failures are logged as warnings to preserve
        the Outbox pattern guarantee: the CommandRecord already exists in
        the DB and will be picked up by a retry/watchdog if dispatch fails.
    """
    try:
        await dispatch_command_task.kiq(str(command_id))
    except Exception as exc:
        logger.warning(
            "Failed to dispatch Taskiq task for command %s: %s",
            command_id,
            exc,
        )
