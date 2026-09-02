import json
from typing import Any

from diagnostics import format_diagnostics_summary, run_host_diagnostics


def register_parser(subparsers: Any) -> None:
    p = subparsers.add_parser("diagnose", help="Сетевая диагностика ПК / IP")
    p.add_argument("target", type=str, help="Имя хоста или IP")
    p.add_argument("--json", action="store_true", help="Вывод в JSON")


async def handle(args: Any) -> None:
    target = args.target.strip()
    diag = await run_host_diagnostics(target)
    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=2))
    else:
        print(format_diagnostics_summary(diag))
