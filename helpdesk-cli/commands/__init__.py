"""
Пакет доменных CLI команд для Helpdesk Agent.
"""
from typing import Any, Callable, Coroutine

from . import (
    catalog,
    diagnose,
    identity,
    kb,
    session,
    tasks,
    triage,
    worker,
)

COMMAND_MODULES = [
    triage,
    tasks,
    identity,
    kb,
    catalog,
    session,
    diagnose,
    worker,
]


def register_all_commands(
    subparsers: Any,
) -> dict[str, Callable[[Any], Coroutine[Any, Any, None]]]:
    """
    Регистрирует аргументы всех команд в subparsers и возвращает словарь диспетчеризации.
    """
    for module in COMMAND_MODULES:
        if hasattr(module, "register_parser"):
            module.register_parser(subparsers)

    dispatch: dict[str, Callable[[Any], Coroutine[Any, Any, None]]] = {
        # Триаж и очередь
        "batch": triage.handle,
        "redirect": triage.handle,
        "duplicates": triage.handle,
        "dedup": triage.handle,
        "queue": triage.handle,
        # Задачи
        "task": tasks.handle,
        "apply": tasks.handle,
        "history": tasks.handle,
        "attachment": tasks.handle,
        "summary": tasks.handle,
        # Active Directory и Учетные записи
        "ad": identity.handle,
        "wlan": identity.handle,
        "create-user": identity.handle,
        "new-user": identity.handle,
        # База знаний и RAG
        "search-kb": kb.handle,
        "sync-kb": kb.handle,
        "check-db": kb.handle,
        "start-db": kb.handle,
        # Каталог
        "catalog": catalog.handle,
        "services": catalog.handle,
        # Сессия
        "skip": session.handle,
        "reset-session": session.handle,
        # Диагностика
        "diagnose": diagnose.handle,
        # Фоновый воркер исполнения
        "worker": worker.handle,
        "run-worker": worker.handle,
    }

    return dispatch
