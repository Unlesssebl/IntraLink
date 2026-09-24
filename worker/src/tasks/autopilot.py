"""Background autopilot execution task for tickets assigned to service bot."""

import logging
from typing import Any, Dict

from worker.src.broker import QUEUE_DEFAULT, broker

logger = logging.getLogger("worker.tasks.autopilot")


@broker.task(task_name="autopilot_task", queue_name=QUEUE_DEFAULT)
async def autopilot_task(task_id: int) -> Dict[str, Any]:
    """Execute autonomous scenario workflow for a ticket assigned to service bot.

    Full autonomous engine and dialogue loop is scheduled for Sprint 5.
    """
    logger.info("Executing autopilot_task for task #%d", task_id)
    return {"status": "enqueued", "task_id": task_id, "stage": "autopilot"}
