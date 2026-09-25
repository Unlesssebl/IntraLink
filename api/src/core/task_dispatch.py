"""Task dispatch gateway: API → Worker boundary via Taskiq broker.

This module is the sole integration point between API and the Worker task queue.
API code MUST use `dispatch_command(command_id)` instead of importing
worker task modules directly. This enforces service isolation:
the API container can operate without mounting the worker/ directory.
"""

import logging
from uuid import UUID

from core.broker import QUEUE_DEFAULT, broker

logger = logging.getLogger("api.core.task_dispatch")

DISPATCH_COMMAND_TASK = "dispatch_command_task"
AUTOPILOT_TASK = "autopilot_task"

# Client-side task handle for enqueuing via Taskiq without importing worker code
dispatch_command_task = broker.register_task(
    lambda command_id: None,
    task_name=DISPATCH_COMMAND_TASK,
    queue_name=QUEUE_DEFAULT,
)

dispatch_autopilot_task_proxy = broker.register_task(
    lambda task_id: None,
    task_name=AUTOPILOT_TASK,
    queue_name=QUEUE_DEFAULT,
)


class TaskDispatchService:
    """Service isolating API from Worker task queue via Taskiq broker proxies."""

    @staticmethod
    async def dispatch_command(command_id: UUID) -> None:
        """Enqueue a CommandRecord for execution by the worker via Taskiq."""
        try:
            await dispatch_command_task.kiq(str(command_id))
        except Exception as exc:
            logger.warning(
                "Failed to dispatch Taskiq task for command %s: %s",
                command_id,
                exc,
            )

    @staticmethod
    async def dispatch_autopilot_task(ticket_id: int) -> None:
        """Enqueue a ticket for autonomous background execution by autopilot_task."""
        try:
            await dispatch_autopilot_task_proxy.kiq(ticket_id)
        except Exception as exc:
            logger.warning(
                "Failed to dispatch Taskiq autopilot task for ticket %s: %s",
                ticket_id,
                exc,
            )


async def dispatch_command(command_id: UUID) -> None:
    """Enqueue a CommandRecord for execution by the worker via Taskiq."""
    await TaskDispatchService.dispatch_command(command_id)


async def dispatch_autopilot_task(ticket_id: int) -> None:
    """Enqueue a ticket for autonomous background execution by autopilot_task."""
    await TaskDispatchService.dispatch_autopilot_task(ticket_id)

