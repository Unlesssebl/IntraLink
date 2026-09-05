"""Reliable relay and stale-lease reconciliation for v2 commands."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging

from sqlalchemy import func, select

from app.database.db import (
    AsyncSessionLocal,
    CommandAttempt,
    CommandEvent,
    CommandOutbox,
    CommandRecord,
)
from app.services.actions.policy import AUTO_ELIGIBLE_ACTIONS
from app.services.command_service import BACKEND_STREAM, WINDOWS_STREAM
from app.services.worker import get_redis_client

logger = logging.getLogger(__name__)


async def relay_once(limit: int = 100) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = list((await db.scalars(
            select(CommandOutbox)
            .where(CommandOutbox.published_at.is_(None), CommandOutbox.available_at <= now)
            .order_by(CommandOutbox.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )).all())
        if not rows:
            return 0
        redis = get_redis_client()
        published = 0
        for row in rows:
            try:
                await redis.xadd(
                    row.stream,
                    {
                        "outbox_id": str(row.id),
                        "command_id": str(row.command_id),
                        "payload": json.dumps(row.payload_json, ensure_ascii=False),
                    },
                    maxlen=10000,
                    approximate=True,
                )
                row.published_at = dt.datetime.now(dt.timezone.utc)
                row.last_error = None
                published += 1
            except Exception as exc:
                row.attempts += 1
                row.last_error = str(exc)[:1000]
                delay = min(300, 5 * (2 ** min(row.attempts, 6)))
                row.available_at = now + dt.timedelta(seconds=delay)
                logger.warning("Outbox publish failed for %s: %s", row.id, exc)
        await db.commit()
        return published


async def reconcile_expired_leases(limit: int = 100) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    async with AsyncSessionLocal() as db:
        commands = list((await db.scalars(
            select(CommandRecord)
            .where(
                CommandRecord.status == "running",
                CommandRecord.lease_expires_at.is_not(None),
                CommandRecord.lease_expires_at < now,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )).all())
        for command in commands:
            attempt = await db.scalar(
                select(CommandAttempt)
                .where(
                    CommandAttempt.command_id == command.id,
                    CommandAttempt.status == "running",
                )
                .order_by(CommandAttempt.attempt_no.desc())
            )
            attempt_no = attempt.attempt_no if attempt else 1
            if attempt:
                attempt.status = "lease_expired"
                attempt.error_message = "Worker lease expired"
                attempt.completed_at = now
            command.version += 1
            command.lease_token_hash = None
            command.lease_expires_at = None
            retry_delay = {1: 5, 2: 30}.get(attempt_no)
            if command.action in AUTO_ELIGIBLE_ACTIONS and retry_delay is not None:
                command.status = "queued"
                stream = WINDOWS_STREAM if command.executor == "windows" else BACKEND_STREAM
                db.add(CommandOutbox(
                    command_id=command.id,
                    stream=stream,
                    payload_json={
                        "command_id": str(command.id),
                        "action": command.action,
                        "executor": command.executor,
                        "version": command.version,
                    },
                    available_at=now + dt.timedelta(seconds=retry_delay),
                ))
                event_type = "lease_expired_requeued"
            else:
                command.status = "needs_review"
                command.completed_at = now
                command.error_message = (
                    "Worker lease expired; execution outcome is unknown or retry limit was reached"
                )
                event_type = "lease_expired_needs_review"
            sequence = int(await db.scalar(
                select(func.max(CommandEvent.sequence)).where(CommandEvent.command_id == command.id)
            ) or 0) + 1
            db.add(CommandEvent(
                command_id=command.id,
                sequence=sequence,
                event_type=event_type,
                details_json={"attempt_no": attempt_no, "retry_in_seconds": retry_delay},
                actor="command-worker",
            ))
        await db.commit()
        return len(commands)


async def run_forever(interval_seconds: float = 1.0) -> None:
    while True:
        try:
            await relay_once()
            await reconcile_expired_leases()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Command worker iteration failed")
        await asyncio.sleep(interval_seconds)
