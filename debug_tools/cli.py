import argparse
import asyncio
import sys

from debug_tools.core import fetch_task_auto
from debug_tools.ai_debugger import run_ai_debugger
from debug_tools.printer_debugger import run_printer_debugger


async def main():
    parser = argparse.ArgumentParser(
        description="Worker Debugger — инструмент LLM-as-judge для диагностики воркеров IntraLink"
    )
    subparsers = parser.add_subparsers(dest="worker", help="Команда для выполнения", required=True)

    # ── ai-worker ──────────────────────────────────────────────
    ai_parser = subparsers.add_parser("ai", help="Отладка AI-воркера (классификация + RAG + автоответ)")
    ai_parser.add_argument("--task-id", type=int, help="ID заявки в IntraService")
    ai_parser.add_argument("--batch", type=str, help="Список ID через запятую: 133609,141205,145001")
    ai_parser.add_argument("--batch-file", type=str, help="Путь к файлу со списком ID (по одному на строку)")
    ai_parser.add_argument("--from-redis", action="store_true", help="Загрузить данные из Redis-кэша вместо Core API")

    # ── printer-worker ─────────────────────────────────────────
    printer_parser = subparsers.add_parser("printer", help="Отладка Printer-воркера (маршрутизация SNMP/Fast/Smart-Track)")
    printer_parser.add_argument("--task-id", type=int, help="ID заявки в IntraService")
    printer_parser.add_argument("--batch", type=str, help="Список ID через запятую: 133609,141205,145001")
    printer_parser.add_argument("--batch-file", type=str, help="Путь к файлу со списком ID (по одному на строку)")
    printer_parser.add_argument("--from-redis", action="store_true", help="Загрузить данные из Redis-кэша вместо Core API")

    # ── approve (эмуляция веб-панели) ──────────────────────────
    approve_parser = subparsers.add_parser("approve", help="Ручное подтверждение/отклонение задачи (эмуляция веб-панели)")
    approve_parser.add_argument("--task-id", type=int, required=True, help="ID заявки")
    approve_parser.add_argument("--action", type=str, choices=["approve", "reject", "update"], required=True)
    approve_parser.add_argument("--target-pc", type=str, help="Новый целевой ПК (для update)")
    approve_parser.add_argument("--model-key", type=str, help="Новая модель (для update)")
    approve_parser.add_argument("--connection-type", type=str, choices=["tcpip", "usb"], help="Тип подключения (для update)")

    # ── redis (диагностика шины) ────────────────────────────────
    subparsers.add_parser("redis", help="Анализ состояния Redis: статистика AI и printer-воркеров")

    # ── stuck (поиск зависших задач) ───────────────────────────
    subparsers.add_parser("stuck", help="Найти зависшие задачи printer-воркера в Redis")

    # ── fix (автофикс зависших задач) ──────────────────────────
    fix_parser = subparsers.add_parser("fix", help="Перезапустить все failed/stuck задачи printer-воркера из Redis")
    fix_parser.add_argument("--dry-run", action="store_true", help="Только показать список, не перезапускать")

    args = parser.parse_args()

    # ── Обработка approve ──────────────────────────────────────
    if args.worker == "approve":
        import redis.asyncio as aioredis
        import json
        from debug_tools.core import REDIS_URL
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        payload = {
            "event_type": "approval_response",
            "task_id": args.task_id,
            "action": args.action,
        }
        if args.target_pc:
            payload["target_pc"] = args.target_pc
        if args.model_key:
            payload["model_key"] = args.model_key
        if args.connection_type:
            payload["connection_type"] = args.connection_type

        await r.publish("printer_actions", json.dumps(payload))
        await r.close()
        print(f"✅ Команда '{args.action}' для заявки #{args.task_id} отправлена в Redis (printer_actions).")
        return

    # ── Обработка redis ────────────────────────────────────────
    if args.worker == "redis":
        from debug_tools.analyze_redis import main as redis_main
        await redis_main()
        return

    # ── Обработка stuck ────────────────────────────────────────
    if args.worker == "stuck":
        from debug_tools.find_stuck import main as stuck_main
        await stuck_main()
        return

    # ── Обработка fix ──────────────────────────────────────────
    if args.worker == "fix":
        from debug_tools.auto_fix import main as fix_main
        await fix_main()
        return

    # ── Батч-режим ─────────────────────────────────────────────
    task_ids = []

    if hasattr(args, "batch") and args.batch:
        task_ids = [int(x.strip()) for x in args.batch.split(",") if x.strip().isdigit()]
    elif hasattr(args, "batch_file") and args.batch_file:
        with open(args.batch_file, "r", encoding="utf-8") as f:
            task_ids = [int(line.strip()) for line in f if line.strip().isdigit()]

    if task_ids:
        from debug_tools.batch_runner import run_batch, print_batch_report
        report = await run_batch(args.worker, task_ids)
        print_batch_report(report)
        return

    # ── Одиночный режим ────────────────────────────────────────
    if not args.task_id:
        print("❌ Укажите --task-id <id> или --batch <ids>")
        sys.exit(1)

    from_redis = getattr(args, "from_redis", False)
    print(f"⬇️  Загрузка заявки #{args.task_id}{'  (из Redis-кэша)' if from_redis else '  (из Core API)'}...")
    try:
        task_data = await fetch_task_auto(args.task_id, from_redis=from_redis)
        if not task_data:
            print("❌ Получены пустые данные заявки.")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Не удалось загрузить заявку: {e}")
        sys.exit(1)

    if args.worker == "ai":
        await run_ai_debugger(task_data)
    elif args.worker == "printer":
        await run_printer_debugger(task_data, args.task_id)


if __name__ == "__main__":
    asyncio.run(main())
