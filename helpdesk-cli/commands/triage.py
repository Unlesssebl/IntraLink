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

    # review-feedback
    p_rf = subparsers.add_parser(
        "review-feedback",
        help="Журнал аудита решений и расхождений для контроля качества (Feedback Loop)",
    )
    p_rf.add_argument("--limit", type=int, default=15, help="Количество записей")
    p_rf.add_argument("--min-diff", type=float, default=0.0, help="Минимальный diff_ratio")
    p_rf.add_argument("--json", action="store_true", help="Вывод в JSON")



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

        print(f"\n================================================================================")
        print(f"{title.upper()} | Всего в очереди: {total_open} | Пачка: {len(items)}")
        print(f"================================================================================")

        if not items:
            print("[INFO] Нет заявок, соответствующих критериям фильтрации.\n")
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
            conf = it.get("confidence_score") or action.get("confidence") or 0.50
            req_review = it.get("requires_human_review", conf < 0.80)

            circuit = (it.get("circuit") or "green").lower()
            if circuit == "red":
                circuit_badge = "[RED / ON-PREM]"
            elif circuit == "yellow":
                circuit_badge = "[SANITIZED]"
            else:
                circuit_badge = "[OPEN]"

            tel = it.get("telemetry") or {}
            ping = tel.get("ping_status")
            if ping == "ONLINE":
                rtt = tel.get("latency_ms")
                host_badge = f"[HOST: {pc} - ONLINE {rtt}ms]" if rtt else f"[HOST: {pc} - ONLINE]"
            elif ping == "OFFLINE":
                host_badge = f"[HOST: {pc} - OFFLINE]"
            elif pc and pc != "—":
                host_badge = f"[HOST: {pc}]"
            else:
                host_badge = ""

            dup_badge = "[ДУБЛИКАТ]" if it.get("is_duplicate") else ""
            conf_badge = f"[LOW CONF: {conf:.2f} / ТРЕБУЕТСЯ ПРОВЕРКА]" if req_review else f"[CONF: {conf:.2f}]"

            status_text = action.get("name", "В работе")
            status_id = action.get("status_id", 27)
            expenses = action.get("expenses", 10)

            badges = [b for b in [f"[STATUS: {status_text.upper()}]", host_badge, dup_badge, circuit_badge, conf_badge] if b]
            badge_line = " | ".join(badges)

            print(f"\n[{idx}] [#{t_id}](https://servicedesk.corporate.loc/Task/View/{t_id}) | {badge_line}")
            print(f"    Тема:       {name}")
            print(f"    Заявитель:  {creator} (каб. {room} | тел. {phone}) | {created}")
            print(f"    Раздел:     {s_name}")
            print(f"    Регламент:  Статус {status_id} ({expenses} мин)")

            attachments = it.get("attachments") or []
            if attachments:
                att_names = ", ".join(a.get("name") or a.get("FileName") or "файл" for a in attachments[:2])
                print(f"    Вложение:   [ATTACHMENT: {att_names} -> вызовите 'скриншот {idx}']")

            if action.get("comment"):
                comment_text = action["comment"].strip()
                print(f"    Ответ заявителю:")
                print(f"    «{comment_text}»")

        print("\n--------------------------------------------------------------------------------")
        print("Действия: 'все' (авто-применение только проверенных >= 0.80) | '1, 3' (выборочно)")
        print("          'детали N' | 'скриншот N' | 'шаблон <имя> для N'")
        print("--------------------------------------------------------------------------------")
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
            print(f"\n=== ПОИСК ДУБЛИКАТОВ В ОЧЕРЕДИ 1-Й ЛИНИИ (Фильтр #{args.filter}) ===")
            print("[INFO] В проверенных открытых заявках дубликатов не обнаружено.\n")
            return

        print(f"\n================================================================================")
        print(f"ОБНАРУЖЕНЫ ДУБЛИКАТЫ В ОЧЕРЕДИ 1-Й ЛИНИИ (Найдено: {len(duplicates)})")
        print(f"================================================================================")
        for idx, d in enumerate(duplicates, 1):
            dup_id = d["duplicate_task_id"]
            m_id = d["master_task_id"]
            reason = d["reason"]
            action = d.get("action", {})
            print(f"[{idx}] [#{dup_id}](https://servicedesk.corporate.loc/Task/View/{dup_id}) | [ДУБЛИКАТ #{m_id}] -> [STATUS: ОТМЕНЕНА]")
            print(f"    Причина: {reason}")
            if action.get("comment"):
                print(f"    Ответ заявителю: «{action.get('comment')}»\n")
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
        print(f"=== ОЧЕРЕДЬ ЗАЯВОК (Фильтр #{args.filter}, Показано: {len(tasks)}) ===")
        for t in tasks:
            print(
                f"[{t['task_id']}] {t['name']} [{t.get('service_name')}] (от {t.get('creator')})"
            )
    finally:
        await client.close()


async def handle_review_feedback(args: Any) -> None:
    client = CoreApiClient()
    try:
        data = await client.get_feedback_review(
            limit=args.limit, min_diff=args.min_diff
        )
        if getattr(args, "json", False):
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return

        items = (data or {}).get("items", [])
        print(f"\n================================================================================")
        print(f"ЖУРНАЛ АУДИТА И ОБРАТНОЙ СВЯЗИ (FEEDBACK LOOP) | Всего записей: {len(items)}")
        print(f"================================================================================")
        if not items:
            print("[INFO] Записей аудита не найдено.\n")
            return

        for idx, it in enumerate(items, 1):
            tid = it.get("task_id")
            score = it.get("confidence_score", 1.0)
            diff = it.get("diff_ratio", 0.0)
            op = it.get("operator_id") or "система"
            date = (it.get("created_at") or "")[:16].replace("T", " ")
            print(f"[{idx}] [#{tid}](https://servicedesk.corporate.loc/Task/View/{tid}) | [CONF: {score:.2f}] | [DIFF: {diff:.2f}] | Оператор: {op} | {date}")
            print(f"    Финальный комментарий:")
            print(f"    «{it.get('final_comment')}»\n")
    finally:
        await client.close()


async def handle(args: Any) -> None:
    if args.command in ("batch", "redirect"):
        await handle_batch(args)
    elif args.command in ("duplicates", "dedup"):
        await handle_duplicates(args)
    elif args.command == "queue":
        await handle_queue(args)
    elif args.command == "review-feedback":
        await handle_review_feedback(args)

