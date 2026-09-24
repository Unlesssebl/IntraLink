"""Worker task definitions."""

from worker.src.tasks.command_dispatcher import dispatch_command_task

__all__ = ["dispatch_command_task"]
