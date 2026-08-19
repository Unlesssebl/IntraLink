import argparse
import asyncio
import json
import os
import sys
from typing import Any
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

from intraservice_api import IntraServiceClient
from diagnostics import extract_potential_hosts, run_host_diagnostics, format_diagnostics_summary
from kb import (
    search_knowledge_base,
    index_task_record,
    sync_history_kb,
)
from template_engine import auto_detect_template, render_template, load_templates

DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "downloads")


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

        # 1. Сетевая диагностика найденных хостов
        hosts = extract_potential_hosts(
            f"{task.get('Name', '')} {task.get('Description', '')}",
            task.get("_field_meta", {}).get("raw", {}),
        )
        diag = None
        if hosts:
            diag = await run_host_diagnostics(hosts[0])

        # 2. RAG поиск
        task_text = f"{task.get('Name', '')}. {task.get('Description', '')}".strip()
        kb_matches = []
        try:
            kb_matches = await search_knowledge_base(task_text, limit=2, distance_threshold=0.70)
        except Exception:
            pass

        # 3. Адаптивный шаблон
        action_plan = auto_detect_template(task, diag, kb_matches)

        task["_diagnostics"] = diag
        task["_kb_matches"] = kb_matches
        task["_suggested_action"] = action_plan

        if args.json:
            print(json.dumps(task, ensure_ascii=False, indent=2))
            return

        print(f"=== Заявка #{task.get('Id')} ===")
        print(f"Тема:       {task.get('Name')}")
        print(f"Заявитель:  {task.get('Creator')}")
        print(f"Раздел:     {task.get('ServiceName')} (ID: {task.get('ServiceId')})")
        print(f"Статус:     {task.get('StatusName')} (ID: {task.get('StatusId')})")
        print(f"Создана:    {task.get('Created')}")
        
        friendly_fields = task.get("_parsed_fields", {})
        if friendly_fields:
            print("Кастомные поля:")
            for k, v in friendly_fields.items():
                print(f"  • {k}: {v}")

        if task.get("_has_attachments"):
            print(f"\n📎 Вложения ({len(task.get('_attachments_list', []))}):")
            for att in task.get("_attachments_list", []):
                print(f"  • ID {att.get('Id')}: {att.get('FileName', 'файл')} ({att.get('Size', 0)} байт)")

        print(f"\nОписание:\n{task.get('Description') or '—'}")

        if diag:
            print(f"\n🌐 Сетевой статус: {format_diagnostics_summary(diag)}")

        comments = task.get("Comments") or []
        if comments:
            print(f"\n--- Комментарии ({len(comments)}) ---")
            for c in comments:
                author = c.get("UserName") or c.get("Creator") or "Пользователь"
                date = c.get("Created", "")
                text = c.get("Text", "").strip()
                print(f"[{date}] {author}:\n{text}\n")

        if kb_matches:
            print(f"--- 🧠 Опыт базы знаний ({len(kb_matches)}) ---")
            for kb in kb_matches:
                print(f"• [Кейс #{kb['task_id']} | Сходство {kb['similarity_pct']}% | {kb['service_name']}]:")
                print(f"  {kb['solution']}\n")

        print("--- 🎯 Предлагаемое действие (Адаптивный шаблон) ---")
        print(f"Шаблон:   {action_plan['name']}")
        print(f"Статус:   #{action_plan['status_id']} ({action_plan['status_name']})")
        print(f"Трудозатраты: {action_plan['expenses']} мин.")
        print(f"Проект ответа:\n{action_plan['comment']}")
    finally:
        await client.close()


async def cmd_diagnose(args):
    target = args.target.strip()
    diag = await run_host_diagnostics(target)
    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=2))
    else:
        print(format_diagnostics_summary(diag))


async def process_single_ticket_for_batch(client: IntraServiceClient, basic_task: dict[str, Any], idx: int) -> dict[str, Any]:
    """Параллельная обработка одного тикета для формирования сводной матрицы."""
    t_id = basic_task.get("Id")
    full_task = await client.get_task_details(t_id) or basic_task

    meta = full_task.get("_field_meta") or {}
    room = meta.get("room") or "—"
    phone = meta.get("phone") or "—"
    creator = full_task.get("Creator", "Заявитель")

    # Сетевая диагностика
    hosts = extract_potential_hosts(
        f"{full_task.get('Name', '')} {full_task.get('Description', '')}",
        meta.get("raw", {}),
    )
    diag = None
    if hosts:
        diag = await run_host_diagnostics(hosts[0])

    # RAG поиск
    task_text = f"{full_task.get('Name', '')}. {full_task.get('Description', '')}".strip()
    kb_matches = []
    try:
        kb_matches = await search_knowledge_base(task_text, limit=1, distance_threshold=0.70)
    except Exception:
        pass

    # Адаптивный шаблон
    action = auto_detect_template(full_task, diag, kb_matches)

    # Форматирование сетевого бейджа
    net_badge = "⚪ Нет хоста"
    if diag:
        if diag.get("is_online"):
            rtt = f" ({diag.get('avg_rtt')})" if diag.get("avg_rtt") else ""
            net_badge = f"🟢 В сети{rtt}"
        else:
            net_badge = f"🔴 Офлайн [{diag.get('target')}]"

    att_count = len(full_task.get("_attachments_list", []))
    att_str = f"📎 {att_count}" if att_count > 0 else "—"

    return {
        "index": idx,
        "task_id": t_id,
        "creator_info": f"{creator}<br>`{room}` / `{phone}`",
        "summary": (full_task.get("Name") or "")[:50],
        "net_badge": net_badge,
        "attachments": att_str,
        "action": action,
        "full_task": full_task,
    }


async def cmd_batch(args):
    """Пакетный анализ стопки заявок со сводной матрицей."""
    client = IntraServiceClient()
    try:
        raw_tasks = await client.get_tasks_by_filter(args.filter, page=args.page, page_size=args.limit)
        if not raw_tasks:
            print("Очередь заявок пуста! Все инциденты обработаны.")
            return

        print(f"Сбор данных и диагностика стопки из {len(raw_tasks)} заявок...")
        tasks_coros = [
            process_single_ticket_for_batch(client, t, idx + 1)
            for idx, t in enumerate(raw_tasks)
        ]
        results = await asyncio.gather(*tasks_coros)

        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return

        print(f"\n### 📋 Очередь 1-й линии (Пачка {args.page}: Заявки 1–{len(results)})\n")
        print("| № | Тикет | Заявитель (Каб / Тел) | Суть инцидента | Сеть хоста | 📎 | Рекомендация (Статус / Комментарий) |")
        print("|---|---|---|---|:---:|:---:|---|")
        
        for r in results:
            t_id = r["task_id"]
            action = r["action"]
            short_comment = action['comment'].split('\n')[0][:40] + "..."
            print(
                f"| **{r['index']}** | **#{t_id}** | {r['creator_info']} | {r['summary']} | "
                f"{r['net_badge']} | {r['attachments']} | "
                f"**[{action['status_id']}: {action['status_name']}]** <br>💬 *«{short_comment}»* |"
            )

        print("\n---")
        print("⚡ **Шорткаты для оператора:**")
        print("• `все` или `+` — применить сразу все рекомендации (1–{0})".format(len(results)))
        print("• `1, 2, 4` — применить выбранные номера")
        print("• `детали <N>` — раскрыть подробную карточку тикета")
        print("• `скриншот <N>` — скачать и посмотреть вложение")
        print("• `шаблон <имя> для <N>` — сменить шаблон (hardware_repair, 1c_issue, printer_issue, wifi_access, pc_offline)")
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
            print("[DRY-RUN] Режим симуляции. Изменения не отправлены.")
            return

        comment_ok = True
        if comment:
            comment_ok = await client.add_comment(task_id, comment)

        status_ok = True
        if status_id:
            status_ok = await client.update_status(task_id, status_id)

        exp_ok = True
        if expenses and expenses > 0:
            exp_ok = await client.add_expenses(task_id, minutes=expenses)

        # Автообучение RAG
        if status_id in (29, 30) and comment and comment.strip():
            try:
                task_data = await client.get_task_details(task_id)
                if task_data:
                    t_name = task_data.get("Name") or f"Заявка #{task_id}"
                    t_desc = task_data.get("Description") or ""
                    s_id = task_data.get("ServiceId") or 0
                    s_name = task_data.get("ServiceName") or "Общие"
                    st_name = "Выполнена" if status_id == 29 else "Отменена"
                    
                    await index_task_record(
                        task_id=task_id,
                        original_name=t_name,
                        problem=f"{t_name}. {t_desc}".strip(),
                        solution=comment.strip(),
                        service_id=s_id,
                        service_name=s_name,
                        status_name=st_name,
                        classification_data={"type": "auto_indexed_by_agent", "status_id": status_id},
                    )
            except Exception:
                pass

        if comment_ok and status_ok and exp_ok:
            print(f"✓ УСПЕХ: Заявка #{task_id} успешно обновлена!")
        else:
            print(f"ОШИБКА обновления заявки #{task_id}", file=sys.stderr)
            sys.exit(1)
    finally:
        await client.close()


async def cmd_search_kb(args):
    """Поиск по RAG базе знаний pgvector."""
    query = args.query.strip()
    matches = await search_knowledge_base(query, limit=args.limit, distance_threshold=args.threshold)

    if args.json:
        print(json.dumps(matches, ensure_ascii=False, indent=2))
        return

    print(f"=== Результаты поиска в базе знаний (Найдено: {len(matches)}) ===")
    if not matches:
        print("Похожих решений в базе знаний не обнаружено.")
        return

    for m in matches:
        print(f"\n• [Кейс #{m['task_id']} | Сходство: {m['similarity_pct']}% | Раздел: {m['service_name']}]")
        print(f"  Тема:     {m['name']}")
        print(f"  Проблема: {m['problem']}")
        print(f"  Решение:\n    {m['solution']}")


async def cmd_sync_kb(args):
    """Пакетная синхронизация закрытых заявок 1-й линии в базу знаний."""
    client = IntraServiceClient()
    try:
        stats = await sync_history_kb(
            is_client=client,
            limit=args.limit,
            days=args.days,
            dry_run=args.dry_run,
        )
        print("\n=== Результаты синхронизации базы знаний ===")
        print(f"Всего проанализировано: {stats['fetched']}")
        print(f"Отобрано по качеству:  {stats['accepted']}")
        print(f"Отсеяно фильтром:      {stats['skipped']}")
        print(f"Успешно проиндексировано: {stats['indexed']}")
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


async def cmd_attachment(args):
    """Скачивание вложений заявки для просмотра оператором/агентом."""
    client = IntraServiceClient()
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
            f_id = att.get("Id") or idx
            save_path = os.path.join(DOWNLOADS_DIR, f"task_{args.task_id}_{name}")
            
            ok = await client.download_attachment_file(args.task_id, f_id, save_path)
            if ok:
                print(f"✓ Скачан: {name} -> {save_path}")
            else:
                print(f"• Файл вложения: {name} (ID: {f_id})")
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description="Helpdesk I/O, Batch Triage & RAG Tools for AGY Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # batch
    p_b = subparsers.add_parser("batch", help="Сводный дашборд стопки заявок с авто-рекомендациями")
    p_b.add_argument("--filter", type=int, default=int(os.getenv("FILTER_ID", "984")), help="ID фильтра очереди")
    p_b.add_argument("--limit", type=int, default=5, help="Размер пачки заявок")
    p_b.add_argument("--page", type=int, default=1, help="Номер пачки/страницы")
    p_b.add_argument("--json", action="store_true", help="Вывод в JSON")

    # task
    p_t = subparsers.add_parser("task", help="Детальная карточка конкретной заявки")
    p_t.add_argument("task_id", type=int, help="ID заявки")
    p_t.add_argument("--json", action="store_true", help="Вывод в JSON")

    # queue
    p_q = subparsers.add_parser("queue", help="Простой список очереди")
    p_q.add_argument("--filter", type=int, default=int(os.getenv("FILTER_ID", "984")), help="ID фильтра очереди")
    p_q.add_argument("--limit", type=int, default=10, help="Количество заявок")
    p_q.add_argument("--json", action="store_true", help="Вывод в JSON")

    # diagnose
    p_d = subparsers.add_parser("diagnose", help="Сетевая диагностика ПК / IP")
    p_d.add_argument("target", type=str, help="Имя хоста или IP")
    p_d.add_argument("--json", action="store_true", help="Вывод в JSON")

    # search-kb
    p_skb = subparsers.add_parser("search-kb", help="Поиск по RAG базе знаний pgvector")
    p_skb.add_argument("query", type=str, help="Текст поискового запроса")
    p_skb.add_argument("--limit", type=int, default=3, help="Лимит совпадений")
    p_skb.add_argument("--threshold", type=float, default=0.70, help="Порог расстояния")
    p_skb.add_argument("--json", action="store_true", help="Вывод в JSON")

    # sync-kb
    p_sykb = subparsers.add_parser("sync-kb", help="Умная синхронизация закрытых заявок")
    p_sykb.add_argument("--limit", type=int, default=50, help="Лимит выгрузки")
    p_sykb.add_argument("--days", type=int, default=30, help="Количество дней")
    p_sykb.add_argument("--dry-run", action="store_true", help="Режим симуляции")

    # apply
    p_a = subparsers.add_parser("apply", help="Применение решения к заявке")
    p_a.add_argument("task_id", type=int, help="ID заявки")
    p_a.add_argument("--status", type=int, required=True, help="Целевой ID статуса")
    p_a.add_argument("--comment", type=str, default="", help="Текст комментария")
    p_a.add_argument("--expenses", type=int, default=0, help="Списание трудозатрат в минутах")
    p_a.add_argument("--dry-run", action="store_true", help="Режим симуляции")

    # catalog
    p_c = subparsers.add_parser("catalog", help="Поиск по каталогу услуг")
    p_c.add_argument("--search", type=str, default=None, help="Поисковый запрос")
    p_c.add_argument("--json", action="store_true", help="Вывод в JSON")

    # history
    p_h = subparsers.add_parser("history", help="История заявки")
    p_h.add_argument("task_id", type=int, help="ID заявки")
    p_h.add_argument("--json", action="store_true", help="Вывод в JSON")

    # attachment
    p_att = subparsers.add_parser("attachment", help="Скачивание вложений заявки")
    p_att.add_argument("task_id", type=int, help="ID заявки")

    args = parser.parse_args()

    dispatch = {
        "batch": cmd_batch,
        "task": cmd_task,
        "queue": cmd_queue,
        "diagnose": cmd_diagnose,
        "search-kb": cmd_search_kb,
        "sync-kb": cmd_sync_kb,
        "apply": cmd_apply,
        "catalog": cmd_catalog,
        "history": cmd_history,
        "attachment": cmd_attachment,
    }

    asyncio.run(dispatch[args.command](args))


if __name__ == "__main__":
    main()
