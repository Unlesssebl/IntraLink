"""
Команды управления жизненным циклом конкретных заявок: task, apply, history, attachment, summary.
"""

import json
import os
import sys
from typing import Any

from core_api_client import CoreApiClient


DOWNLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "downloads"
)


def register_parser(subparsers: Any) -> None:
    # task
    p_t = subparsers.add_parser(
        "task", help="Детальная карточка конкретной заявки"
    )
    p_t.add_argument("task_id", type=int, help="ID заявки")
    p_t.add_argument("--json", action="store_true", help="Вывод в JSON")

    # apply
    p_a = subparsers.add_parser(
        "apply",
        help="Применение решения к заявке (или списку ID через запятую)",
    )
    p_a.add_argument(
        "task_id",
        type=str,
        help="ID заявки или список ID через запятую (например 139088,138972)",
    )
    p_a.add_argument(
        "--status", type=int, required=True, help="Целевой ID статуса"
    )
    p_a.add_argument(
        "--comment", type=str, default="", help="Текст комментария"
    )
    p_a.add_argument(
        "--expenses",
        type=int,
        default=0,
        help="Списание трудозатрат в минутах",
    )
    p_a.add_argument(
        "--executor",
        type=str,
        default="8664,10502",
        help="ID исполнителей",
    )
    p_a.add_argument(
        "--dry-run", action="store_true", help="Режим симуляции"
    )

    # history
    p_h = subparsers.add_parser("history", help="История заявки")
    p_h.add_argument("task_id", type=int, help="ID заявки")
    p_h.add_argument("--json", action="store_true", help="Вывод в JSON")

    # attachment
    p_att = subparsers.add_parser(
        "attachment", help="Скачивание вложений заявки"
    )
    p_att.add_argument("task_id", type=int, help="ID заявки")

    # summary
    p_s = subparsers.add_parser(
        "summary",
        help="AI-суммаризация истории и переписки инцидента через Qwen2.5:1.5B",
    )
    p_s.add_argument("task_id", type=int, help="ID заявки")
    p_s.add_argument("--json", action="store_true", help="Вывод в JSON")


async def handle_task(args: Any) -> None:
    client = CoreApiClient()
    try:
        card = await client.get_task_card(args.task_id)
        if not card or not card.get("task"):
            print(f"❌ Заявка #{args.task_id} не найдена в IntraService.", file=sys.stderr)
            sys.exit(1)

        if getattr(args, "json", False):
            print(json.dumps(card, ensure_ascii=False, indent=2))
            return

        task = card["task"]
        history = card.get("history", [])
        kb_matches = card.get("kb_matches", [])
        action = card.get("suggested_action") or {}
        conf = card.get("confidence_score") or action.get("confidence") or 0.50
        req_review = card.get("requires_human_review", conf < 0.80)
        conf_str = f"[LOW CONFIDENCE: {conf:.2f} / ТРЕБУЕТСЯ ПРОВЕРКА]" if req_review else f"[CONFIDENCE: {conf:.2f}]"

        meta = task.get("_field_meta") or {}
        created = (task.get("Created") or "")[:16].replace("T", " ")

        print(f"\n=======================================================")
        print(f"КАРТОЧКА ЗАЯВКИ [#{args.task_id}](https://servicedesk.corporate.loc/Task/View/{args.task_id}) | {conf_str}")
        print(f"=======================================================")
        print(f"Тема:        {task.get('Name')}")
        print(f"Раздел:      {task.get('ServiceName')} (ID: {task.get('ServiceId')})")
        print(f"Статус:      {task.get('StatusName')} (ID: {task.get('StatusId')})")
        print(f"Дата:        {created}")
        print(f"Заявитель:   {task.get('Creator')} (Логин: {task.get('CreatorLogin')})")
        print(f"Телефон:     {meta.get('phone') or task.get('CreatorPhone') or '—'}")
        print(f"Кабинет:     {meta.get('room') or '—'}")
        print(f"ПК / Хост:   {meta.get('pc_name') or '—'}")
        print(f"\nОписание:\n{task.get('Description') or '—'}\n")

        if kb_matches:
            print("Похожие решения из базы знаний (RAG):")
            for m in kb_matches:
                print(f"  [Кейс #{m['task_id']} | Сходство: {m['similarity_pct']}%] {m['name']}")
                print(f"  Решение: {m['solution'][:100]}...\n")

        print(f"Рекомендованное действие:")
        print(f"  Шаблон:         {action.get('name')}")
        print(f"  Целевой статус: {action.get('status_id')} ({action.get('expenses', 10)} мин)")
        print(f"  Комментарий:\n    {action.get('comment')}\n")
    finally:
        await client.close()


async def handle_apply(args: Any) -> None:
    client = CoreApiClient()
    try:
        raw_ids = str(args.task_id).split(",")
        task_ids = [int(x.strip()) for x in raw_ids if x.strip().isdigit()]
        if not task_ids:
            print(f"[ERROR] Некорректный ID заявки: '{args.task_id}'", file=sys.stderr)
            sys.exit(1)

        status_id = args.status
        comment = args.comment
        expenses = args.expenses
        executor_ids = getattr(args, "executor", None) or "8664,10502"
        dry_run = args.dry_run

        # Если применяется пакет заявок (> 1), проверяем Confidence Score для защиты от Rubber Stamping
        if len(task_ids) > 1:
            validated_task_ids = []
            skipped_low_conf = []
            for tid in task_ids:
                card = await client.get_task_card(tid)
                c_score = (card or {}).get("confidence_score", 1.0) if card else 1.0
                if c_score < 0.80:
                    skipped_low_conf.append((tid, c_score))
                else:
                    validated_task_ids.append(tid)

            if skipped_low_conf:
                print("\n[ВНИМАНИЕ] Следующие заявки имеют низкую уверенность (< 0.80) и пропущены при пакетном применении:")
                for stid, sc in skipped_low_conf:
                    print(f"  - Заявка #{stid}: Confidence {sc:.2f} (требуется ручная проверка)")
                print("Для применения к этим заявкам подтвердите их точечно командой с конкретным ID.\n")

            task_ids = validated_task_ids
            if not task_ids:
                print("[INFO] Нет заявок с достаточным уровнем уверенности для пакетного применения.")
                return

        print(f"=== Применение решения к заявкам: {', '.join(f'#{tid}' for tid in task_ids)} ===")
        print(f"Целевой статус: {status_id} | Списание: {expenses} мин | Исполнители: {executor_ids}")

        # Маршрутизация через Command Bus для доменных действий и Core API
        for task_id in task_ids:
            # 1. Автовыдача Wi-Fi в Active Directory через Command Bus
            if status_id == 29 and any(w in (comment or "").lower() for w in ["wi-fi", "wifi", "вайфай", "вай-фай"]):
                print(f"[Command Bus] Постановка задачи Wi-Fi для #{task_id}...")
                cmd_res = await client.submit_command(
                    command_type="grant_wlan",
                    target={"task_id": task_id},
                    params={"auto_close": True, "executor_ids": executor_ids},
                    mode="dry_run" if dry_run else "auto",
                    source="cli",
                )
                if cmd_res and cmd_res.get("status") in ("accepted", "dry_run_success"):
                    print(f"[OK] Задача Wi-Fi #{task_id} зарегистрирована в Command Bus ({cmd_res.get('job_id', 'dry_run')})")
                    continue

            # 2. Создание пользователя в Active Directory через Command Bus
            elif status_id == 29 and ("{password}" in (comment or "") or "учетная запись успешно создана" in (comment or "").lower()):
                print(f"[Command Bus] Постановка задачи создания пользователя для #{task_id}...")
                cmd_res = await client.submit_command(
                    command_type="create_user",
                    target={"task_id": task_id},
                    params={"auto_close": True, "executor_ids": executor_ids},
                    mode="dry_run" if dry_run else "auto",
                    source="cli",
                )
                if cmd_res and cmd_res.get("status") in ("accepted", "dry_run_success"):
                    print(f"[OK] Задача New User #{task_id} зарегистрирована в Command Bus ({cmd_res.get('job_id', 'dry_run')})")
                    continue

            # Обычные заявки (смена статуса, комментарий, списание)
            ok = await client.apply_decision(
                task_ids=[task_id],
                status_id=status_id,
                comment=comment or "",
                expenses=expenses,
                executor_ids=executor_ids,
                dry_run=dry_run,
            )
            if ok:
                print(f"[OK] Заявка #{task_id}: решение успешно применено через Core API.")
            else:
                print(f"[ERROR] Ошибка применения решения для #{task_id}.", file=sys.stderr)

        print(f"[DONE] Обработка пачки завершена.")
    finally:
        await client.close()



async def handle_history(args: Any) -> None:
    client = CoreApiClient()
    try:
        history = await client.get_task_history(args.task_id)
        if getattr(args, "json", False):
            print(json.dumps(history, ensure_ascii=False, indent=2))
            return

        print(f"=== История заявки #{args.task_id} (Записей: {len(history)}) ===")
        for h in history:
            date = h.get("Date") or h.get("Created") or h.get("EventDate", "")
            user = h.get("Editor") or h.get("UserName") or h.get("Creator", "Система")
            desc = h.get("Comments") or h.get("Comment") or h.get("Description") or h.get("EventName", "")
            print(f"[{date}] {user}: {desc}")
    finally:
        await client.close()


async def handle_attachment(args: Any) -> None:
    client = CoreApiClient()
    try:
        task = await client.get_task_details(args.task_id)
        if not task:
            print(f"Заявка #{args.task_id} не найдена.")
            return

        attachments = task.get("_attachments_list", [])
        if not attachments:
            print(f"В заявке #{args.task_id} нет прикрепленных файлов.")
            return

        print(f"=== Вложения заявки #{args.task_id} ({len(attachments)}) ===")
        for idx, att in enumerate(attachments, 1):
            name = att.get("FileName", f"file_{idx}")
            print(f"• [{idx}] Вложение: {name}")
    finally:
        await client.close()


async def handle_summary(args: Any) -> None:
    client = CoreApiClient()
    try:
        task_id = args.task_id
        task_card = await client.get_task_card(task_id)
        if not task_card or not task_card.get("task"):
            print(f"❌ Заявка #{task_id} не найдена.", file=sys.stderr)
            sys.exit(1)

        task = task_card["task"]
        history = task_card.get("history", [])

        comments = []
        for item in history:
            txt = (item.get("Comments") or item.get("Comment") or item.get("Description") or "").strip()
            if txt:
                comments.append({
                    "UserName": item.get("Editor") or item.get("UserName") or item.get("Creator") or "Пользователь",
                    "Created": item.get("Date") or item.get("Created") or "",
                    "Text": txt,
                })

        summary = await client.ai_summarize(
            task_id=task_id,
            task_name=task.get("Name") or "",
            task_desc=task.get("Description") or "",
            comments=comments,
        )

        if getattr(args, "json", False):
            print(json.dumps(summary or {}, ensure_ascii=False, indent=2))
            return

        if summary:
            print(f"\n### 📋 AI-Сводка по заявке #{task_id} (Core API AI Hub)\n")
            print(f"• **Суть инцидента:** {summary.get('core_problem', 'N/A')}")
            print(f"• **Текущий статус:** {summary.get('current_status', 'N/A')}")
            print(f"• **Рекомендованный шаг:** {summary.get('recommended_next_step', 'N/A')}\n")
        else:
            print("⚠️ Не удалось получить AI-сводку (AI Hub Core API временно недоступен).", file=sys.stderr)
    finally:
        await client.close()


async def handle(args: Any) -> None:
    if args.command == "task":
        await handle_task(args)
    elif args.command == "apply":
        await handle_apply(args)
    elif args.command == "history":
        await handle_history(args)
    elif args.command == "attachment":
        await handle_attachment(args)
    elif args.command == "summary":
        await handle_summary(args)
