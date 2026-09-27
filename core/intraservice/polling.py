"""Read-only queue polling contracts shared with the worker."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from core.intraservice.dto import TaskDTO


def get_executor_ids_list(task: TaskDTO) -> list[int]:
    """Parse the comma-separated IntraService executor identifiers."""
    if not task.executor_ids:
        return []
    return [int(part) for part in str(task.executor_ids).split(",") if part.strip().isdigit()]


class PollerStepResult(BaseModel):
    polled_at: datetime
    filter_tasks_count: int
    changed_tasks_count: int
    unique_tasks_count: int
    observed_tasks_count: int
    next_interval_sec: float
    consecutive_errors: int = 0
    error: str | None = None


PollStepResult = PollerStepResult
