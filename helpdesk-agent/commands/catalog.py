"""
Команды каталога услуг и рубрикатора IntraService: catalog, services.
"""

import json
from typing import Any

from core_api_client import CoreApiClient


def register_parser(subparsers: Any) -> None:
    # services
    p_s = subparsers.add_parser(
        "services", help="Список корневых разделов каталога с номерами"
    )
    p_s.add_argument("--json", action="store_true", help="Вывод в JSON")

    # catalog
    p_c = subparsers.add_parser("catalog", help="Поиск по каталогу услуг")
    p_c.add_argument(
        "--search", type=str, default=None, help="Поисковый запрос"
    )
    p_c.add_argument("--json", action="store_true", help="Вывод в JSON")


async def handle_services(args: Any) -> None:
    client = CoreApiClient()
    try:
        services = await client.get_services()
        if getattr(args, "json", False):
            print(json.dumps(services, ensure_ascii=False, indent=2))
            return

        print("=== 📂 Корневые разделы каталога услуг IntraService ===")
        print("Используйте номер раздела в командах `/triage <номер>`:")
        for s in services:
            print(f"  • {s['name']}")
    finally:
        await client.close()


async def handle_catalog(args: Any) -> None:
    client = CoreApiClient()
    try:
        catalog = await client.get_catalog(search=getattr(args, "search", None))
        if getattr(args, "json", False):
            print(json.dumps(catalog, ensure_ascii=False, indent=2))
            return

        print(f"=== Каталог услуг IntraService (Найдено: {len(catalog)}) ===")
        for s in catalog:
            print(
                f"• ID {s.get('Id')}: {s.get('Name')} (Parent: {s.get('ParentId')})"
            )
    finally:
        await client.close()


async def handle(args: Any) -> None:
    if args.command == "services":
        await handle_services(args)
    elif args.command == "catalog":
        await handle_catalog(args)
