import argparse
import asyncio
import sys

from debug_tools.core import fetch_task
from debug_tools.ai_debugger import run_ai_debugger
from debug_tools.printer_debugger import run_printer_debugger

async def main():
    parser = argparse.ArgumentParser(description="Worker Debugger - Инструмент для диагностики заявок IntraService")
    subparsers = parser.add_subparsers(dest="worker", help="Воркер для диагностики", required=True)

    # ai-worker
    ai_parser = subparsers.add_parser("ai", help="Отладка AI-воркера")
    ai_parser.add_argument("--task-id", type=int, required=True, help="ID заявки в IntraService")

    # printer-worker
    printer_parser = subparsers.add_parser("printer", help="Отладка Printer-воркера")
    printer_parser.add_argument("--task-id", type=int, required=True, help="ID заявки в IntraService")

    # approve
    approve_parser = subparsers.add_parser("approve", help="Ручное подтверждение заявки (эмуляция веб-панели)")
    approve_parser.add_argument("--task-id", type=int, required=True, help="ID заявки")
    approve_parser.add_argument("--action", type=str, choices=["approve", "reject", "update"], required=True, help="Действие")
    approve_parser.add_argument("--target-pc", type=str, help="Новый целевой ПК (для update)")
    approve_parser.add_argument("--model-key", type=str, help="Новая модель (для update)")
    approve_parser.add_argument("--connection-type", type=str, choices=["tcpip", "usb"], help="Тип подключения (для update)")

    args = parser.parse_args()

    if args.worker == "approve":
        import redis.asyncio as aioredis
        import json
        r = aioredis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
        payload = {
            "event_type": "approval_response",
            "task_id": args.task_id,
            "action": args.action
        }
        if args.target_pc: payload["target_pc"] = args.target_pc
        if args.model_key: payload["model_key"] = args.model_key
        if args.connection_type: payload["connection_type"] = args.connection_type
        
        await r.publish("printer_actions", json.dumps(payload))
        print(f"✅ Команда {args.action} для заявки #{args.task_id} отправлена в Redis (printer_actions).")
        return

    print(f"⬇️ Скачивание данных заявки #{args.task_id} из Core API...")
    try:
        task_data = await fetch_task(args.task_id)
        if not task_data:
            print("❌ Ошибка: Получены пустые данные заявки. Возможно, такой заявки не существует.")
            sys.exit(1)
    except Exception as e:
        print(f"❌ Не удалось скачать заявку: {e}")
        sys.exit(1)

    if args.worker == "ai":
        await run_ai_debugger(task_data)
    elif args.worker == "printer":
        await run_printer_debugger(task_data, args.task_id)

if __name__ == "__main__":
    asyncio.run(main())
