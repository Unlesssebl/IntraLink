import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("helpdesk_agent.session_state")

SESSION_FILE = os.path.join(os.path.dirname(__file__), ".session_state.json")
SESSION_TTL_SEC = 28800.0  # 8 часов (одна рабочая смена)


def _load_session() -> dict[str, Any]:
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                now = time.time()
                # Очистка устаревших записей (> 8 часов)
                skipped = data.get("skipped_tasks", {})
                fresh_skipped = {
                    k: v for k, v in skipped.items()
                    if now - v.get("timestamp", 0) < SESSION_TTL_SEC
                }
                data["skipped_tasks"] = fresh_skipped
                return data
        except Exception as e:
            logger.debug("Ошибка чтения .session_state.json: %s", e)
    return {"skipped_tasks": {}, "applied_tasks": {}}


def _save_session(data: dict[str, Any]):
    try:
        temp_file = SESSION_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, SESSION_FILE)
    except Exception as e:
        logger.error("Ошибка сохранения .session_state.json: %s", e)


def add_skipped_tasks(task_ids: list[int | str], reason: str = "operator_skipped"):
    """Добавляет ID тикетов в список пропущенных/отложенных в текущей смене."""
    data = _load_session()
    now = time.time()
    for tid in task_ids:
        data["skipped_tasks"][str(tid)] = {
            "reason": reason,
            "timestamp": now,
        }
    _save_session(data)


def add_applied_tasks(task_ids: list[int | str], status_id: int):
    """Фиксирует примененные заявки и удаляет их из списка пропущенных."""
    data = _load_session()
    now = time.time()
    for tid in task_ids:
        s_tid = str(tid)
        data["applied_tasks"][s_tid] = {
            "status_id": status_id,
            "timestamp": now,
        }
        if s_tid in data["skipped_tasks"]:
            del data["skipped_tasks"][s_tid]
    _save_session(data)


def get_skipped_task_ids() -> set[int]:
    """Возвращает множество ID пропущенных задач."""
    data = _load_session()
    res = set()
    for str_id in data.get("skipped_tasks", {}).keys():
        try:
            res.add(int(str_id))
        except ValueError:
            pass
    return res


def reset_session_state() -> bool:
    """Сбрасывает сессионный кэш пропущенных и обработанных задач."""
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        return True
    except Exception as e:
        logger.error("Ошибка сброса .session_state.json: %s", e)
        return False
