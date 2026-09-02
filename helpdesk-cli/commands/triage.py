"""
Команды пакетного триажа, очереди и дедупликации заявок.
"""

import json
import os
import sys
from typing import Any

from core_api_client import CoreApiClient


def register_parser(subparsers: Any) -> None:
    # batch
    p_b = subparsers.add_parser(
        "batch", help="Сводный дашборд стопки заявок с авто-рекомендациями"
    )
    p_b.add_argument(
        "--filter",
        type=int,
        default=int(os.getenv("FILTER_ID", "984")),
        help="ID фильтра очереди",
    )
    p_b.add_argument(
        "--service",
        "-s",
        type=str,
        default=None,
        help="Номер раздела (01..16, 2, 3, 6) или название сервиса для фильтрации",
    )
    p_b.add_argument(
        "--limit", type=int, default=5, help="Размер пачки заявок"
    )
    p_b.add_argument(
        "--page", type=int, default=1, help="Номер пачки/страницы"
    )
    p_b.add_argument(
        "--redirect",
        "-r",
        action="store_true",
        help="Поиск только заявок, требующих отмены и редиректа в другие сервисы",
    )
    p_b.add_argument(
        "--include-skipped",
        action="store_true",
        help="Включить в выборку ранее пропущенные заявки",
    )
    p_b.add_argument("--json", action="store_true", help="Вывод в JSON")

    # redirect
    p_r = subparsers.add_parser(
        "redirect",
        help="Поиск и отмена заявок, требующих редиректа в другие сервисы",
    )
    p_r.add_argument(
        "--filter",
        type=int,
        default=int(os.getenv("FILTER_ID", "984")),
        help="ID фильтра очереди",
    )
    p_r.add_argument(
        "--service",
        "-s",
        type=str,
        default=None,
        help="Номер раздела (01..16, 2, 3, 6) или название сервиса для фильтрации",
    )
    p_r.add_argument(
        "--limit", type=int, default=5, help="Размер пачки заявок"
    )
    p_r.add_argument(
        "--page", type=int, default=1, help="Номер пачки/страницы"
    )
    p_r.add_argument(
        "--include-skipped",
        action="store_true",
        help="Включить в выборку ранее пропущенные заявки",
    )
    p_r.add_argument("--json", action="store_true", help="Вывод в JSON")

    # duplicates
    p_d = subparsers.add_parser(
        "duplicates",
        aliases=["dedup"],
        help="Поиск и отмена заявок-дубликатов в очереди 1-й линии",
    )
    p_d.add_argument(
        "--filter",
        type=int,
        default=int(os.getenv("FILTER_ID", "984")),
        help="ID фильтра очереди",
    )
    p_d.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Максимальное количество дубликатов для вывода",
    )
    p_d.add_argument("--json", action="store_true", help="Вывод в JSON")

    # queue
    p_q = subparsers.add_parser("queue", help="Простой список очереди")
    p_q.add_argument(
        "--filter",
        type=int,
        default=int(os.getenv("FILTER_ID", "984")),
        help="ID фильтра",
    )
    p_q.add_argument(
        "--limit", type=int, default=25, help="Количество заявок"
    )
    p_q.add_argument("--json", action="store_true", help="Вывод в JSON")


async def handle_batch(args: Any) -> None:
    client = CoreApiClient()
    try:
        redirect_mode = getattr(args, "redirect", False) or args.command == "redirect"
        data = await client.get_triage_batch(
            filter_id=args.filter,
            limit=args.limit,
            page=args.page,
            service_prefix=args.service,
            redirect_only=redirect_mode,
            include_skipped=getattr(args, "include_skipped", False),
        )

        if getattr(args, "json", False):
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return

        items = data.get("tasks", [])
        total_open = data.get("total_open", 0)

        title = "Стопка заявок 1-й линии"
        if redirect_mode:
            title = "Заявки для отмены / редиректа в другие сервисы"
        if args.service:
            title += f" [Раздел: {args.service}]"

        print(
            f"\n=== 📥 {title} (Всего в очереди: {total_open} | Пачка: {len(items)}) ==="
        )

        if not items:
            print("🎉 Нет заявок, соответствующих критериям фильтрации.\n")
            return

        for idx, it in enumerate(items, 1):
            t_id = it["task_id"]
            name = it["name"]
            created = (it.get("created") or "")[:16].replace("T", " ")
            s_name = it.get("service_name") or "—"
            creator = it.get("creator") or "—"
            phone = it.get("creator_phone") or "—"
            room = it.get("room") or "—"
            pc = it.get("pc_name") or "—"
            action = it.get("suggested_action") or {}

            dup_badge = " 🔴 [ДУБЛИКАТ]" if it.get("is_duplicate") else ""
            print(
                f"\n[{idx}] 🎫 [#{t_id}](https://servicedesk.corporate.loc/Task/View/{t_id}) | 📅 {created} | 📂 {s_name}{dup_badge}"
            )
            print(f"    • Тема: {name}")
            print(
                f"    • Заявитель: {creator} | 📍 {room} | 📞 `{phone}` | 💻 `{pc}`"
            )
            print(
                f"    • 🎯 Рекомендация: **{action.get('name', 'В работе')}** ➔ Статус {action.get('status_id')} ({action.get('expenses', 10)} мин)"
            )
            if action.get("comment"):
                first_line = action["comment"].split("\n")[0]
                print(f"    • 💬 *«{first_line}»*")

        print("\n---")
        print("⚡ Шорткаты: `все` или `+` (применить все) | `1, 2` (выборочно) | `детали <N>`")
    finally:
        await client.close()


async def handle_duplicates(args: Any) -> None:
    client = CoreApiClient()
    try:
        duplicates = await client.get_duplicates(
            filter_id=args.filter, limit=args.limit
        )
        if getattr(args, "json", False):
            print(json.dumps(duplicates, ensure_ascii=False, indent=2))
            return

        if not duplicates:
            print(
                f"\n=== 🔍 Поиск дубликатов в очереди 1-й линии (Фильтр #{args.filter}) ==="
            )
            print("✅ В проверенных открытых заявках дубликатов не обнаружено.\n")
            return

        print(
            f"\n### 🗑️ Обнаружены заявки-дубликаты в очереди 1-й линии (Найдено: {len(duplicates)})\n"
        )
        for idx, d in enumerate(duplicates, 1):
            dup_id = d["duplicate_task_id"]
            m_id = d["master_task_id"]
            reason = d["reason"]
            action = d.get("action", {})
            print(
                f"### [{idx}] [#{dup_id}](https://servicedesk.corporate.loc/Task/View/{dup_id}) ➔ 🎯 **Отменена (Дубликат #{m_id})**"
            )
            print(f"- **Причина:** {reason}")
            print(f"> 💬 *«{action.get('comment', '')}»*\n")
    finally:
        await client.close()


async def handle_queue(args: Any) -> None:
    client = CoreApiClient()
    try:
        data = await client.get_triage_batch(
            filter_id=args.filter, limit=args.limit, page=1
        )
        if getattr(args, "json", False):
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return

        tasks = data.get("tasks", [])
        print(f"=== Очередь заявок (Фильтр #{args.filter}, Показано: {len(tasks)}) ===")
        for t in tasks:
            print(
                f"• #{t['task_id']}: {t['name']} [{t.get('service_name')}] (от {t.get('creator')})"
            )
    finally:
        await client.close()


async def handle(args: Any) -> None:
    if args.command in ("batch", "redirect"):
        await handle_batch(args)
    elif args.command in ("duplicates", "dedup"):
        await handle_duplicates(args)
    elif args.command == "queue":
        await handle_queue(args)
