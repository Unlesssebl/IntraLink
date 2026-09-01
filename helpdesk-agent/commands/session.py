"""
Команды управления сессионным состоянием смены оператора: skip, reset-session.
"""

import sys
from typing import Any

from core_api_client import CoreApiClient


def register_parser(subparsers: Any) -> None:
    # skip
    p_sk = subparsers.add_parser(
        "skip", help="Пропуск заявки в текущей сессии"
    )
    p_sk.add_argument(
        "task_id",
        type=str,
        help="ID заявки или список ID через запятую",
    )
    p_sk.add_argument(
        "--reason",
        type=str,
        default="operator_skipped",
        help="Причина пропуска",
    )

    # reset-session
    subparsers.add_parser(
        "reset-session", help="Сброс сессионного кэша пропущенных заявок"
    )


async def handle_skip(args: Any) -> None:
    client = CoreApiClient()
    try:
        raw_ids = [x.strip() for x in str(args.task_id).split(",") if x.strip()]
        task_ids = []
        for x in raw_ids:
            try:
                task_ids.append(int(x))
            except ValueError:
                pass
        if not task_ids:
            print("❌ Не указаны валидные ID заявок для пропуска.", file=sys.stderr)
            sys.exit(1)

        ok = await client.skip_tasks(
            task_ids, reason=args.reason or "operator_skipped"
        )
        if ok:
            print(
                f"✓ Заявки #{', #'.join(str(x) for x in task_ids)} помечены как пропущенные в текущей смене."
            )
            print("💡 Они не будут появляться в /triage и /redirect до сброса (`reset-session`).")
    finally:
        await client.close()


async def handle_reset_session(args: Any) -> None:
    client = CoreApiClient()
    try:
        ok = await client.reset_session()
        if ok:
            print("✓ Сессионное состояние сброшено в Core API. Все пропущенные заявки снова активны.")
    finally:
        await client.close()


async def handle(args: Any) -> None:
    if args.command == "skip":
        await handle_skip(args)
    elif args.command == "reset-session":
        await handle_reset_session(args)
