"""Background triage task for unassigned tickets."""

import logging
from typing import Any, Dict

from worker.src.broker import QUEUE_DEFAULT, broker

logger = logging.getLogger("worker.tasks.triage")


@broker.task(task_name="triage_task", queue_name=QUEUE_DEFAULT)
async def triage_task(task_id: int) -> Dict[str, Any]:
    """Execute triage analysis and gateway checks for an unassigned ticket.

    Full deterministic and LLM triage implementation is scheduled for Sprint 4.
    """
    logger.info("Executing triage_task for task #%d", task_id)
    return {"status": "enqueued", "task_id": task_id, "stage": "triage"}
