"""
Команды работы с базой знаний и RAG: search-kb, sync-kb, check-db, start-db.
Все запросы поиска и синхронизации RAG идут через Core API.
"""

import asyncio
import json
import os
import subprocess
import sys
from typing import Any

from core_api_client import CoreApiClient


def register_parser(subparsers: Any) -> None:
    # search-kb
    p_skb = subparsers.add_parser(
        "search-kb", help="Поиск по RAG базе знаний pgvector через Core API"
    )
    p_skb.add_argument("query", type=str, help="Текст поискового запроса")
    p_skb.add_argument(
        "--limit", type=int, default=3, help="Лимит совпадений"
    )
    p_skb.add_argument(
        "--threshold", type=float, default=0.70, help="Порог расстояния"
    )
    p_skb.add_argument("--json", action="store_true", help="Вывод в JSON")

    # sync-kb
    p_sykb = subparsers.add_parser(
        "sync-kb", help="Умная синхронизация закрытых заявок в RAG"
    )
    p_sykb.add_argument(
        "--limit", type=int, default=50, help="Лимит выгрузки"
    )
    p_sykb.add_argument(
        "--days", type=int, default=30, help="Количество дней"
    )

    # check-db
    p_cdb = subparsers.add_parser(
        "check-db",
        help="Проверка статуса подключения к Core API Gateway и PostgreSQL",
    )
    p_cdb.add_argument(
        "--exit-code",
        action="store_true",
        help="Вернуть exit code 1 при недоступности",
    )

    # start-db
    subparsers.add_parser(
        "start-db",
        help="Автоматический запуск PostgreSQL, LiteLLM и Core API в Docker",
    )


async def handle_search_kb(args: Any) -> None:
    client = CoreApiClient()
    try:
        query = args.query.strip()
        matches = await client.search_kb(
            query=query, limit=args.limit, threshold=args.threshold
        )

        if getattr(args, "json", False):
            print(json.dumps(matches, ensure_ascii=False, indent=2))
            return

        print(
            f"=== Результаты поиска в базе знаний pgvector (Найдено: {len(matches)}) ==="
        )
        if not matches:
            print("Похожих решений в базе знаний не обнаружено.")
            return

        for m in matches:
            print(
                f"\n• [Кейс #{m['task_id']} | Сходство: {m.get('similarity_pct')}% | Раздел: {m.get('service_name')}]"
            )
            print(f"  Тема:     {m.get('name')}")
            print(f"  Проблема: {m.get('problem')}")
            print(f"  Решение:\n    {m.get('solution')}")
    finally:
        await client.close()


async def handle_check_db(args: Any) -> None:
    client = CoreApiClient()
    try:
        matches = await client.search_kb("тест", limit=1)
        print("=== Проверка статуса базы знаний (RAG) ===")
        print("🟢 Tier 1: Core API Gateway и PostgreSQL pgvector активны.")
        print(
            f"✓ Семантический поиск и векторная база доступны на {client.base_url}"
        )
    except Exception as e:
        print(f"🔴 Core API или PostgreSQL недоступны: {e}", file=sys.stderr)
        print("\n💡 Для запуска выполните: `uv run python helpdesk_tool.py start-db`")
        if getattr(args, "exit_code", False):
            sys.exit(1)
    finally:
        await client.close()


def _run_docker_compose_up(root_dir: str) -> tuple[int, str, str]:
    cmd = ["docker", "compose", "up", "-d", "postgres", "redis", "core-api"]
    proc = subprocess.run(
        cmd, cwd=root_dir, capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


async def handle_start_db(args: Any) -> None:
    root_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "..")
    )
    print(
        "🚀 Запуск контейнеров PostgreSQL, Redis и Core API через Docker Compose..."
    )

    try:
        returncode, stdout, stderr = await asyncio.to_thread(
            _run_docker_compose_up, root_dir
        )
        if returncode != 0:
            print(f"🔴 Ошибка запуска Docker: {stderr}", file=sys.stderr)
            sys.exit(1)
        print("✓ Контейнеры Docker Compose успешно запущены.")
    except Exception as e:
        print(f"🔴 Не удалось запустить Docker: {e}", file=sys.stderr)
        sys.exit(1)


async def handle_sync_kb(args: Any) -> None:
    client = CoreApiClient()
    try:
        days = getattr(args, "days", 30)
        limit = getattr(args, "limit", 50)
        print(
            f"🔄 Запуск синхронизации закрытых заявок за последние {days} дн. (Лимит: {limit}) в RAG базу знаний..."
        )
        res = await client.sync_kb(days=days, limit=limit)
        if res.get("status") == "success":
            print("✓ Синхронизация успешно выполнена:")
            print(f"  • Получено заявок из IntraService: {res.get('total_fetched', 0)}")
            print(f"  • Закрытых инцидентов:             {res.get('total_closed', 0)}")
            print(f"  • Новых проиндексировано в RAG:    {res.get('indexed', 0)}")
            print(f"  • Пропущено (ранее добавлены):     {res.get('skipped', 0)}")
        else:
            print(f"⚠️ Ошибка синхронизации: {res}", file=sys.stderr)
    finally:
        await client.close()


async def handle(args: Any) -> None:
    if args.command == "search-kb":
        await handle_search_kb(args)
    elif args.command == "check-db":
        await handle_check_db(args)
    elif args.command == "start-db":
        await handle_start_db(args)
    elif args.command == "sync-kb":
        await handle_sync_kb(args)
