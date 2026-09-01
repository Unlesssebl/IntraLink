"""
Команда запуска фонового воркера исполнения в среде Windows (Active Directory / WinRM / DNS).
"""

from typing import Any
from execution_worker import WindowsExecutionWorker


def register_parser(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "worker",
        aliases=["run-worker"],
        help="Запуск фонового демона исполнения задач из Redis Streams (AD, WLAN, WinRM)",
    )


async def handle(args: Any) -> None:
    worker = WindowsExecutionWorker()
    try:
        await worker.start()
    except KeyboardInterrupt:
        await worker.stop()
