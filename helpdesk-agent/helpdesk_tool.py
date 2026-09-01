"""
Helpdesk I/O, Batch Triage & RAG CLI Tool for Antigravity Agent.
Модульная точка входа CLI.
"""
import argparse
import asyncio
import os
import sys
from dotenv import load_dotenv

# Обеспечиваем корректный импорт модулей helpdesk-agent из любой рабочей директории
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Загрузка переменных окружения
load_dotenv()

from commands import register_all_commands


def main():
    parser = argparse.ArgumentParser(
        description="Helpdesk I/O, Batch Triage & RAG Tools for AGY Agent"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    dispatch = register_all_commands(subparsers)

    args = parser.parse_args()

    handler = dispatch.get(args.command)
    if not handler:
        parser.print_help()
        sys.exit(1)

    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
