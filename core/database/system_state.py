"""Dual watermark persistence (PostgreSQL + Redis) for ingestion tracking and cursors."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis.asyncio as aioredis
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.database.models import SystemState
from core.database.session import get_engine, get_session_factory

logger = logging.getLogger("core.system_state")

_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def set_system_state_session_factory(factory: Optional[async_sessionmaker[AsyncSession]]) -> None:
    """Override session factory for testing environments."""
    global _override_session_factory
    _override_session_factory = factory


def _get_active_session_factory() -> async_sessionmaker[AsyncSession]:
    if _override_session_factory is not None:
        return _override_session_factory
    engine = get_engine()
    return get_session_factory(engine)


class WatermarkDTO(BaseModel):
    """Normalized watermark metadata transfer object."""

    key: str
    last_poll_at: Optional[datetime] = None
    last_task_id: Optional[int] = None
    state_data: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime] = None

    @field_validator("last_poll_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class WatermarkService:
    """Manages dual watermark cursor state across Redis (fast-tier) and PostgreSQL (durability tier)."""

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        redis_client: Optional[aioredis.Redis] = None,
    ) -> None:
        self._session_factory = session_factory
        self._redis_client = redis_client

    def _get_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory or _get_active_session_factory()

    async def get_watermark(
        self,
        key: str,
        session: Optional[AsyncSession] = None,
        redis_client: Optional[aioredis.Redis] = None,
    ) -> WatermarkDTO:
        """Fetch watermark cursor.

        Checks Redis cache first (0ms latency).
        On cache miss, reads from PostgreSQL and populates Redis cache.
        """
        redis = redis_client or self._redis_client

        # 1. Fast path: Redis cache
        if redis is not None:
            try:
                cached_json = await redis.get(f"system_state:{key}")
                if cached_json:
                    data = json.loads(cached_json)
                    return WatermarkDTO.model_validate(data)
            except Exception as exc:
                logger.warning("Failed to fetch watermark '%s' from Redis cache: %s", key, exc)

        # 2. Durable path: PostgreSQL
        if session is not None:
            return await self._read_db_watermark(key, session, redis)

        factory = self._get_factory()
        async with factory() as db_session:
            return await self._read_db_watermark(key, db_session, redis)

    async def _read_db_watermark(
        self,
        key: str,
        session: AsyncSession,
        redis: Optional[aioredis.Redis],
    ) -> WatermarkDTO:
        stmt = select(SystemState).where(SystemState.key == key)
        record = (await session.execute(stmt)).scalar_one_or_none()

        if record is None:
            dto = WatermarkDTO(key=key, last_poll_at=None, last_task_id=None, state_data={})
        else:
            dto = WatermarkDTO(
                key=record.key,
                last_poll_at=record.last_poll_at,
                last_task_id=record.last_task_id,
                state_data=record.state_data or {},
                updated_at=record.updated_at,
            )

        # Warm up Redis cache
        if redis is not None:
            try:
                await redis.set(f"system_state:{key}", dto.model_dump_json())
            except Exception as exc:
                logger.debug("Failed to warm up Redis cache for watermark '%s': %s", key, exc)

        return dto

    async def set_watermark(
        self,
        key: str,
        last_poll_at: Optional[datetime] = None,
        last_task_id: Optional[int] = None,
        state_data: Optional[Dict[str, Any]] = None,
        session: Optional[AsyncSession] = None,
        redis_client: Optional[aioredis.Redis] = None,
    ) -> WatermarkDTO:
        """Persist updated watermark cursor to both PostgreSQL and Redis."""
        redis = redis_client or self._redis_client

        if session is not None:
            dto = await self._write_db_watermark(
                key=key,
                last_poll_at=last_poll_at,
                last_task_id=last_task_id,
                state_data=state_data,
                session=session,
            )
        else:
            factory = self._get_factory()
            async with factory() as db_session:
                dto = await self._write_db_watermark(
                    key=key,
                    last_poll_at=last_poll_at,
                    last_task_id=last_task_id,
                    state_data=state_data,
                    session=db_session,
                )

        # Update Redis cache and compatibility keys
        if redis is not None:
            try:
                await redis.set(f"system_state:{key}", dto.model_dump_json())
                if key == "ingestion_poller":
                    if last_poll_at is not None:
                        await redis.set("worker:last_check_time", last_poll_at.isoformat())
                    if last_task_id is not None:
                        await redis.set("worker:service_last_task_id", str(last_task_id))
            except Exception as exc:
                logger.warning("Failed to persist watermark '%s' into Redis cache: %s", key, exc)

        return dto

    async def _write_db_watermark(
        self,
        key: str,
        last_poll_at: Optional[datetime],
        last_task_id: Optional[int],
        state_data: Optional[Dict[str, Any]],
        session: AsyncSession,
    ) -> WatermarkDTO:
        stmt = select(SystemState).where(SystemState.key == key)
        record = (await session.execute(stmt)).scalar_one_or_none()
        now = datetime.now(timezone.utc)

        if record is not None:
            if last_poll_at is not None:
                record.last_poll_at = last_poll_at
            if last_task_id is not None:
                record.last_task_id = last_task_id
            if state_data is not None:
                record.state_data = dict(state_data)
            record.updated_at = now
        else:
            record = SystemState(
                key=key,
                last_poll_at=last_poll_at,
                last_task_id=last_task_id,
                state_data=state_data or {},
            )
            record.updated_at = now
            session.add(record)

        await session.commit()
        await session.refresh(record)

        return WatermarkDTO(
            key=record.key,
            last_poll_at=record.last_poll_at,
            last_task_id=record.last_task_id,
            state_data=record.state_data or {},
            updated_at=record.updated_at,
        )
