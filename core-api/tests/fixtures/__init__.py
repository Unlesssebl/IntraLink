"""
Пакет тестовых фикстур и синтетических данных IntraLink.
"""
from .mock_ai_tickets import (
    MOCK_AI_TICKETS,
    get_mock_tasks,
    get_mock_task_by_id,
    get_mock_tasks_by_circuit,
    get_mock_tasks_by_category,
)

__all__ = [
    "MOCK_AI_TICKETS",
    "get_mock_tasks",
    "get_mock_task_by_id",
    "get_mock_tasks_by_circuit",
    "get_mock_tasks_by_category",
]
