import argparse
import asyncio
import json
import os
import sys
from dotenv import load_dotenv

# Загрузка переменных окружения из .env
load_dotenv()

from intraservice_api import IntraServiceClient
from agent_brain import HelpdeskAgentBrain, STATUS_IN_PROGRESS, STATUS_NEED_CLARIFICATION, STATUS_RESOLVED, STATUS_CANCELED, STATUS_WAIT_DEVICE
from diagnostics import run_host_diagnostics, extract_potential_hosts, format_diagnostics_summary


async def cmd_queue(args):
    client = IntraServiceClient()
    try:
        tasks = await client.get_tasks_by_filter(args.filter, page=1, page_size=args.limit)
        if args.json:
            print(json.dumps(tasks, ensure_ascii=False, indent=2))
            return

        print(f"=== Очередь заявок (Фильтр #{args.filter}, Найдено: {len(tasks)}) ===")
        for t in tasks:
            t_id = t.get("Id")
            name = t.get("Name")
            creator = t.get("Creator")
            service = t.get("ServiceName") or f"ID {t.get('ServiceId')}"
            status = t.get("StatusName") or f"ID {t.get('StatusId')}"
            created = t.get("Created", "")
            print(f"• #{t_id} | {status} | {service} | {creator} | {name} ({created})")
    finally:
        await client.close()


async def cmd_task(args):
    client = IntraServiceClient()
    try:
        task = await client.get_task_details(args.task_id)
        if not task:
            print(f"Заявка #{args.task_id} не найдена.", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(task, ensure_ascii=False, indent=2))
            return

        print(f"=== Заявка #{task.get('Id')} ===")
        print(f"Тема:       {task.get('Name')}")
        print(f"Заявитель:  {task.get('Creator')}")
        print(f"Раздел:     {task.get('ServiceName')} (ID: {task.get('ServiceId')})")
        print(f"Статус:     {task.get('StatusName')} (ID: {task.get('StatusId')})")
        print(f"Создана:    {task.get('Created')}")
        print(f"Кастомные поля: {task.get('_parsed_fields', {})}")
        print(f"Описание:\n{task.get('Description') or '—'}")

        comments = task.get("Comments") or []
        if comments:
            print(f"\n--- Комментарии ({len(comments)}) ---")
            for c in comments:
                author = c.get("UserName") or c.get("Creator") or "Пользователь"
                date = c.get("Created", "")
                text = c.get("Text", "").strip()
                print(f"[{date}] {author}:\n{text}\n")
    finally:
        await client.close()


async def cmd_diagnose(args):
    target = args.target.strip()
    diag = await run_host_diagnostics(target)
    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=2))
    else:
        print(format_diagnostics_summary(diag))


async def cmd_suggest(args):
    client = IntraServiceClient()
    brain = HelpdeskAgentBrain()
    try:
        catalog = await client.get_service_catalog()
        brain.set_service_catalog(catalog)

        task = await client.get_task_details(args.task_id)
        if not task:
            print(f"Заявка #{args.task_id} не найдена.", file=sys.stderr)
            sys.exit(1)

        decision = await brain.analyze_and_decide(task)
        res_dict = decision.model_dump()
        res_dict["task_id"] = args.task_id
        res_dict["task_name"] = task.get("Name")
        res_dict["task_creator"] = task.get("Creator")
        res_dict["task_service"] = task.get("ServiceName")

        if args.json:
            print(json.dumps(res_dict, ensure_ascii=False, indent=2))
            return

        print(f"=== Предложенное решение для #{args.task_id} ===")
        print(f"Целевой статус:  {decision.new_status_name} (ID: {decision.new_status_id})")
        print(f"Уверенность:     {int(decision.confidence * 100)}%")
        print(f"Обоснование:     {decision.reason}")
        if decision.diagnostics_info:
            print(f"Диагностика:     {decision.diagnostics_info}")
        if decision.correct_service_name:
            print(f"Правильный раздел: {decision.correct_service_name} (ID: {decision.correct_service_id})")
        print(f"\nПредлагаемый комментарий:\n{decision.proposed_comment}")
    finally:
        await client.close()


async def cmd_apply(args):
    client = IntraServiceClient()
    try:
        task_id = args.task_id
        status_id = args.status
        comment = args.comment
        expenses = args.expenses
        dry_run = args.dry_run

        print(f"=== Применение решения к заявке #{task_id} ===")
        print(f"Целевой статус ID: {status_id}")
        print(f"Трудозатраты:     {expenses} мин." if expenses else "Трудозатраты: не списываются")
        print(f"Комментарий:\n{comment}\n")

        if dry_run:
            print("[DRY-RUN] Изменения НЕ отправлены в IntraService (режим симуляции).")
            return

        # 1. Добавляем комментарий
        comment_ok = True
        if comment:
            comment_ok = await client.add_comment(task_id, comment)

        # 2. Обновляем статус
        status_ok = True
        if status_id:
            status_ok = await client.update_status(task_id, status_id)

        # 3. Списываем трудозатраты (если указано)
        exp_ok = True
        if expenses and expenses > 0:
            exp_ok = await client.add_expenses(task_id, minutes=expenses)

        if comment_ok and status_ok and exp_ok:
            print(f"УСПЕХ: Заявка #{task_id} успешно обновлена в IntraService!")
        else:
            print(f"ОШИБКА: comment_ok={comment_ok}, status_ok={status_ok}, exp_ok={exp_ok}", file=sys.stderr)
            sys.exit(1)
    finally:
        await client.close()


async def cmd_catalog(args):
    client = IntraServiceClient()
    try:
        catalog = await client.get_service_catalog()
        if args.search:
            q = args.search.lower()
            catalog = [s for s in catalog if q in (s.get("Name") or "").lower()]

        if args.json:
            print(json.dumps(catalog, ensure_ascii=False, indent=2))
            return

        print(f"=== Каталог услуг IntraService (Найдено: {len(catalog)}) ===")
        for s in catalog:
            print(f"• ID {s.get('Id')}: {s.get('Name')} (Parent: {s.get('ParentId')})")
    finally:
        await client.close()


async def cmd_history(args):
    client = IntraServiceClient()
    try:
        history = await client.get_task_history(args.task_id)
        if args.json:
            print(json.dumps(history, ensure_ascii=False, indent=2))
            return

        print(f"=== История заявки #{args.task_id} (Записей: {len(history)}) ===")
        for h in history:
            date = h.get("Created") or h.get("EventDate", "")
            user = h.get("UserName") or h.get("Creator", "Система")
            desc = h.get("Description") or h.get("EventName", "")
            print(f"[{date}] {user}: {desc}")
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description="Helpdesk Tool for AGY Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # queue
    p_q = subparsers.add_parser("queue", help="Просмотр очереди заявок")
    p_q.add_argument("--filter", type=int, default=int(os.getenv("FILTER_ID", "984")), help="ID фильтра очереди")
    p_q.add_argument("--limit", type=int, default=10, help="Количество заявок")
    p_q.add_argument("--json", action="store_true", help="Вывод в JSON")

    # task
    p_t = subparsers.add_parser("task", help="Просмотр данных конкретной заявки")
    p_t.add_argument("task_id", type=int, help="ID заявки")
    p_t.add_argument("--json", action="store_true", help="Вывод в JSON")

    # diagnose
    p_d = subparsers.add_parser("diagnose", help="Сетевая диагностика ПК / IP")
    p_d.add_argument("target", type=str, help="Имя хоста или IP")
    p_d.add_argument("--json", action="store_true", help="Вывод в JSON")

    # suggest
    p_s = subparsers.add_parser("suggest", help="Формирование решения и комментария ИИ")
    p_s.add_argument("task_id", type=int, help="ID заявки")
    p_s.add_argument("--json", action="store_true", help="Вывод в JSON")

    # apply
    p_a = subparsers.add_parser("apply", help="Применение решения к заявке")
    p_a.add_argument("task_id", type=int, help="ID заявки")
    p_a.add_argument("--status", type=int, required=True, help="Целевой ID статуса")
    p_a.add_argument("--comment", type=str, default="", help="Текст комментария")
    p_a.add_argument("--expenses", type=int, default=0, help="Списание трудозатрат в минутах")
    p_a.add_argument("--dry-run", action="store_true", help="Режим симуляции")

    # catalog
    p_c = subparsers.add_parser("catalog", help="Просмотр и поиск по каталогу услуг")
    p_c.add_argument("--search", type=str, default=None, help="Поисковый запрос")
    p_c.add_argument("--json", action="store_true", help="Вывод в JSON")

    # history
    p_h = subparsers.add_parser("history", help="Просмотр истории заявки")
    p_h.add_argument("task_id", type=int, help="ID заявки")
    p_h.add_argument("--json", action="store_true", help="Вывод в JSON")

    args = parser.parse_args()

    dispatch = {
        "queue": cmd_queue,
        "task": cmd_task,
        "diagnose": cmd_diagnose,
        "suggest": cmd_suggest,
        "apply": cmd_apply,
        "catalog": cmd_catalog,
        "history": cmd_history,
    }

    asyncio.run(dispatch[args.command](args))


if __name__ == "__main__":
    main()
