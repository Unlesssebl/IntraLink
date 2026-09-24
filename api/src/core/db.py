"""SQLAlchemy 2.0 Async Database Engine and Session Management."""

import logging
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from api.src.core.config import settings
from core.database.session import get_engine, get_session_factory

logger = logging.getLogger(__name__)

engine: AsyncEngine = get_engine(
    database_url=settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = get_session_factory(engine)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_health() -> bool:
    """Check database connectivity and responsiveness."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning(f"Database health check failed: {exc}")
        return False


async def dispose_db() -> None:
    """Gracefully close all pooled database connections."""
    await engine.dispose()
