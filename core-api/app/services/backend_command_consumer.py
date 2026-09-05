"""Backend executor for v2 commands that do not belong on a Windows node."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from fastapi import HTTPException

from app.database.db import AsyncSessionLocal
from app.routers.deps import get_service_auth_b64
from app.services.command_service import BACKEND_STREAM, CommandService
from app.services.rag import sync_historical_closed_tasks
from app.services.triage_service import TriageService
from app.services.worker import get_redis_client

logger = logging.getLogger(__name__)

GROUP = "backend_execution_group"
CONSUMER = f"backend_{os.getenv('HOSTNAME', 'local')}"


async def _execute(action: str, values: dict, db) -> dict:
    service_auth = await get_service_auth_b64(
        authorization=None, admin_session=None, token_query=None
    )
    if action == "rag_sync":
        return await sync_historical_closed_tasks(
            auth_b64=service_auth,
            db=db,
            days=int(values.get("days", 30)),
            limit=int(values.get("limit", 50)),
        )
    if action == "apply_triage":
        results = await TriageService.apply_triage_resolution(
            service_auth_b64=service_auth,
            db=db,
            task_ids=[int(value) for value in values.get("task_ids", [])],
            status_id=int(values["status_id"]),
            comment=str(values.get("comment", "")),
            expenses=int(values.get("expenses", 0)),
            executor_ids=values.get("executor_ids"),
            operator_user_id=values.get("operator_user_id"),
            verified_execution_job_id=values.get("verified_execution_job_id"),
        )
        if not results or any(not item.get("update_ok", False) for item in results):
            raise RuntimeError("One or more triage updates failed")
        return {"results": results}
    raise RuntimeError(f"Unsupported backend action: {action}")


async def _process_message(redis, message_id: str, data: dict) -> bool:
    command_id = uuid.UUID(str(data.get("command_id")))
    outbox_raw = str(data.get("outbox_id") or "")
    outbox_id = uuid.UUID(outbox_raw) if outbox_raw else None
    async with AsyncSessionLocal() as db:
        service = CommandService(db)
        try:
            claim = await service.claim(
                command_id,
                worker_id=CONSUMER,
                message_id=message_id,
                outbox_id=outbox_id,
                lease_seconds=900,
            )
        except HTTPException as exc:
            return exc.status_code == 409

        values = dict(claim.command.target_json or {})
        values.update(claim.command.params_json or {})
        try:
            result = await _execute(claim.command.action, values, db)
            outcome = "succeeded"
            error = None
        except Exception as exc:
            await db.rollback()
            logger.exception("Backend command %s failed", command_id)
            result = {}
            outcome = "failed"
            error = str(exc)[:1000]

        try:
            await CommandService(db).finish(
                command_id,
                claim_token=claim.token,
                outcome=outcome,
                result=result,
                error_message=error,
                worker_id=CONSUMER,
            )
            return True
        except Exception:
            logger.exception("Could not persist result for backend command %s", command_id)
            return False


async def run_backend_consumer() -> None:
    redis = get_redis_client()
    try:
        await redis.xgroup_create(BACKEND_STREAM, GROUP, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    while True:
        events = await redis.xreadgroup(
            groupname=GROUP,
            consumername=CONSUMER,
            streams={BACKEND_STREAM: ">"},
            count=5,
            block=3000,
        )
        if not events:
            claimed = await redis.xautoclaim(
                BACKEND_STREAM,
                GROUP,
                CONSUMER,
                min_idle_time=900_000,
                start_id="0-0",
                count=5,
            )
            events = [(BACKEND_STREAM, claimed[1])] if claimed and claimed[1] else []
        for stream, messages in events:
            for message_id, data in messages:
                try:
                    if await _process_message(redis, message_id, data):
                        await redis.xack(stream, GROUP, message_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Backend message %s remains pending", message_id)
