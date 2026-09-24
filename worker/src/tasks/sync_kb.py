"""Knowledge Base synchronization task executed via Taskiq on rag_compute queue."""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.database.session import get_engine, get_session_factory
from core.rag.sync import KnowledgeBaseSyncService
from worker.src.broker import QUEUE_RAG_COMPUTE, broker

logger = logging.getLogger("worker.tasks.sync_kb")

# Session factory hook (allows test overrides)
_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def set_session_factory(factory: Optional[async_sessionmaker[AsyncSession]]) -> None:
    """Override session factory for testing environments."""
    global _override_session_factory
    _override_session_factory = factory


def _get_active_session_factory() -> async_sessionmaker[AsyncSession]:
    if _override_session_factory is not None:
        return _override_session_factory
    engine = get_engine()
    return get_session_factory(engine)


@broker.task(
    task_name="sync_kb_task",
    queue_name=QUEUE_RAG_COMPUTE,
    schedule=[
        {
            "cron": "0 2 * * *",
            "schedule_id": "sync_kb_daily_02_00",
        }
    ],
)
async def sync_kb_task(
    hours: int = 48,
    status_ids: Optional[List[int]] = None,
    quota_per_service: int = 30,
    auth_b64: Optional[str] = None,
    ai_eval: bool = True,
) -> Dict[str, Any]:
    """Scheduled Taskiq background job running daily at 02:00 UTC."""
    logger.info(
        "Executing sync_kb_task on %s: hours=%d, quota_per_service=%d, ai_eval=%s",
        QUEUE_RAG_COMPUTE,
        hours,
        quota_per_service,
        ai_eval,
    )
    session_factory = _get_active_session_factory()
    sync_service = KnowledgeBaseSyncService()

    async with session_factory() as session:
        result = await sync_service.sync_incremental(
            session=session,
            hours=hours,
            status_ids=status_ids,
            quota_per_service=quota_per_service,
            auth_b64=auth_b64,
            ai_eval=ai_eval,
        )
        return result.model_dump()


async def sync_closed_tickets_task(
    batch_size: int = 100,
    hours: int = 48,
    quota_per_service: int = 30,
    session: Optional[AsyncSession] = None,
    auth_b64: Optional[str] = None,
    ai_eval: bool = True,
) -> Dict[str, Any]:
    """Compatibility entrypoint for CommandRecord dispatchers and manual runs."""
    sync_service = KnowledgeBaseSyncService()

    if session is not None:
        result = await sync_service.sync_incremental(
            session=session,
            hours=hours,
            quota_per_service=quota_per_service,
            auth_b64=auth_b64,
            ai_eval=ai_eval,
        )
        return result.model_dump()

    session_factory = _get_active_session_factory()
    async with session_factory() as active_session:
        result = await sync_service.sync_incremental(
            session=active_session,
            hours=hours,
            quota_per_service=quota_per_service,
            auth_b64=auth_b64,
            ai_eval=ai_eval,
        )
        return result.model_dump()

