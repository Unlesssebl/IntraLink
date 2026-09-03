"""
Команда мониторинга состояния фонового воркера исполнения (Windows Execution Worker).
"""
import sys
from typing import Any
from core_api_client import CoreApiClient


def register_parser(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "worker",
        aliases=["worker-status"],
        help="Проверка статуса и доступности Windows Execution Worker",
    )
    p.add_argument("--json", action="store_true", help="Вывод в JSON")


async def handle(args: Any) -> None:
    client = CoreApiClient()
    try:
        status_res = await client.get_command_status("worker-health")
        print("=== 🖥️  Статус Windows Execution Worker ===")
        print("💡 Демон исполнения запускается отдельно: python execution-worker/worker.py")
    finally:
        await client.close()
