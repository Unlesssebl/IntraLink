"""Database session factory and engine lifecycle management for core."""

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DB_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/intraservice"


def get_engine(
    database_url: str | None = None,
    echo: bool = False,
    pool_size: int = 10,
    max_overflow: int = 20,
) -> AsyncEngine:
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DB_URL)
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


async def get_db_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory(engine)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
