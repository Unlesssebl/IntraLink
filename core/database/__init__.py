"""SQLAlchemy base models and database session management."""

from core.database.base import GUID, Base, TimestampMixin
from core.database.models import (
    CommandRecord,
    SystemState,
    TaskKnowledgeBase,
    TriageAudit,
    User,
)
from core.database.session import get_db_session, get_engine, get_session_factory
from core.database.system_state import (
    WatermarkDTO,
    WatermarkService,
    set_system_state_session_factory,
)

__all__ = [
    "Base",
    "GUID",
    "TimestampMixin",
    "CommandRecord",
    "SystemState",
    "TaskKnowledgeBase",
    "TriageAudit",
    "User",
    "WatermarkDTO",
    "WatermarkService",
    "set_system_state_session_factory",
    "get_engine",
    "get_session_factory",
    "get_db_session",
]
