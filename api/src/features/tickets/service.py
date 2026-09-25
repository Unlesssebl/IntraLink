"""Business logic and IntraService orchestration for Tickets feature slice."""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.config import settings
from api.src.core.redis import get_redis_client
from core.autopilot.intent import detect_tense_tone
from core.database.models import CommandRecord
from core.intraservice import (
    IntraServiceClient,
    TaskDTO,
    TaskLifetimeEventDTO,
    sanitize_ticket_description,
)

from .schemas import (
    AddCommentRequest,
    CommandActionResponse,
    CommandStatusResponse,
    ExecuteTicketActionRequest,
    TicketDetailDTO,
    TicketSummaryDTO,
    UpdateTicketRequest,
)

logger = logging.getLogger(__name__)


class TicketService:
    """Service handling ticket read and write operations."""

    def __init__(self, client: Optional[IntraServiceClient] = None) -> None:
        self.client = client or IntraServiceClient(
            base_url=settings.INTRASERVICE_URL,
            verify_ssl=settings.SSL_VERIFY,
        )

    async def _invalidate_ticket_cache(self, ticket_id: int) -> None:
        """Evict cached ticket details and lifetime events from Redis."""
        try:
            redis = get_redis_client()
            await redis.delete(f"cache:ticket:{ticket_id}", f"cache:ticket:{ticket_id}:lifetime")
        except Exception as exc:
            logger.warning("Redis ticket cache invalidation failed: %s", exc)

    async def list_tickets(
        self,
        filter_id: int = 984,  # Default 1st line queue filter
        page: int = 1,
        page_size: int = 100,
        auth_b64: Optional[str] = None,
    ) -> List[TicketSummaryDTO]:
        tasks = await self.client.get_tasks_by_filter(
            filter_id=filter_id,
            page=page,
            page_size=page_size,
            auth_b64=auth_b64,
        )
        # Batch fetch prefetched plans from Redis for instant enrichment
        redis = get_redis_client()
        plans_by_id: Dict[int, Any] = {}
        if redis is not None and tasks:
            try:
                plan_keys = [f"cache:autopilot:plan:{t.id}" for t in tasks]
                cached_plans = await redis.mget(plan_keys)
                for t, raw_plan in zip(tasks, cached_plans):
                    if raw_plan:
                        try:
                            plans_by_id[t.id] = json.loads(raw_plan)
                        except Exception:
                            pass
            except Exception as exc:
                logger.debug("Failed to mget cached plans from Redis: %s", exc)

        results = []
        for t in tasks:
            plan = plans_by_id.get(t.id)
            if plan:
                is_tense = plan.get("is_tense", False)
                tense_reason = plan.get("tense_reason")
                has_attachments = plan.get("has_attachments", bool(t.attachments))
                scenario_key = plan.get("scenario_key")
                scenario_name = plan.get("scenario_name")
                confidence = plan.get("confidence")
            else:
                is_tense, tense_reason = detect_tense_tone(f"{t.name} {t.description or ''}")
                has_attachments = bool(t.attachments)
                scenario_key = None
                scenario_name = None
                confidence = None

            results.append(
                TicketSummaryDTO(
                    id=t.id,
                    name=t.name,
                    description=sanitize_ticket_description(t.description, max_chars=4000),
                    service_id=t.service_id,
                    service_name=t.service_name,
                    status_id=t.status_id,
                    status_name=t.status_name,
                    priority_name=t.priority_name,
                    created=t.created,
                    applicant_name=t.applicant_name,
                    pc_name=t.entities.pc_name if t.entities else None,
                    is_tense=is_tense,
                    tense_reason=tense_reason,
                    has_attachments=has_attachments,
                    scenario_key=scenario_key,
                    scenario_name=scenario_name,
                    confidence=confidence,
                )
            )
        return results

    async def get_ticket(self, ticket_id: int, auth_b64: Optional[str] = None) -> TicketDetailDTO:
        cache_key = f"cache:ticket:{ticket_id}"
        redis = get_redis_client()
        try:
            cached_data = await redis.get(cache_key)
            if cached_data:
                return TicketDetailDTO.model_validate_json(cached_data)
        except Exception as exc:
            logger.warning("Redis ticket cache read failed: %s", exc)

        task: TaskDTO = await self.client.get_task(task_id=ticket_id, auth_b64=auth_b64)
        detail = TicketDetailDTO(
            id=task.id,
            name=task.name,
            description=sanitize_ticket_description(task.description),
            service_id=task.service_id,
            service_name=task.service_name,
            status_id=task.status_id,
            status_name=task.status_name,
            priority_name=task.priority_name,
            created=task.created,
            creator_name=task.creator_name,
            applicant_name=task.applicant_name,
            executor_ids=task.executor_ids,
            entities=task.entities.model_dump() if task.entities else {},
            custom_fields=task.custom_fields,
            attachments=[a.model_dump() for a in task.attachments],
        )

        try:
            await redis.set(cache_key, detail.model_dump_json(), ex=60)
        except Exception as exc:
            logger.warning("Redis ticket cache write failed: %s", exc)

        return detail

    async def get_ticket_lifetime(self, ticket_id: int, auth_b64: Optional[str] = None) -> List[Dict[str, Any]]:
        cache_key = f"cache:ticket:{ticket_id}:lifetime"
        redis = get_redis_client()
        try:
            cached_data = await redis.get(cache_key)
            if cached_data:
                return json.loads(cached_data)
        except Exception as exc:
            logger.warning("Redis ticket lifetime cache read failed: %s", exc)

        events: List[TaskLifetimeEventDTO] = await self.client.get_task_lifetime(task_id=ticket_id, auth_b64=auth_b64)
        result = [e.model_dump() for e in events]

        try:
            await redis.set(cache_key, json.dumps(result, default=str), ex=60)
        except Exception as exc:
            logger.warning("Redis ticket lifetime cache write failed: %s", exc)

        return result

    async def update_ticket(
        self,
        ticket_id: int,
        req: UpdateTicketRequest,
        auth_b64: Optional[str] = None,
    ) -> bool:
        success = await self.client.update_task(
            task_id=ticket_id,
            status_id=req.status_id,
            comment=req.comment,
            executor_ids=req.executor_ids,
            is_private=req.is_private,
            auth_b64=auth_b64,
        )
        if success:
            await self._invalidate_ticket_cache(ticket_id)
        return success

    async def add_comment(
        self,
        ticket_id: int,
        req: AddCommentRequest,
        auth_b64: Optional[str] = None,
    ) -> bool:
        success = await self.client.add_task_comment(
            task_id=ticket_id,
            comment=req.comment,
            is_private=req.is_private,
            auth_b64=auth_b64,
        )
        if success:
            await self._invalidate_ticket_cache(ticket_id)
        return success

    async def execute_action(
        self,
        ticket_id: int,
        req: ExecuteTicketActionRequest,
        initiator: str,
        session: AsyncSession,
        auth_b64: Optional[str] = None,
    ) -> CommandActionResponse:
        """Create an idempotent command record in PostgreSQL (Outbox pattern)."""
        idempotency_key = req.idempotency_key or f"ticket-{ticket_id}-{req.action}-{uuid.uuid4().hex[:8]}"

        # Check existing command by idempotency key
        stmt = select(CommandRecord).where(CommandRecord.idempotency_key == idempotency_key)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            return CommandActionResponse(
                command_id=existing.id,
                idempotency_key=existing.idempotency_key,
                action=existing.action,
                status=existing.status,
                created_at=existing.created_at,
            )

        cmd = CommandRecord(
            id=uuid.uuid4(),
            idempotency_key=idempotency_key,
            action=req.action,
            executor="api",
            target_json={"ticket_id": ticket_id},
            params_json=req.params,
            status="pending",
            initiator=initiator,
            task_id=ticket_id,
        )
        session.add(cmd)
        await session.commit()
        await session.refresh(cmd)

        try:
            from api.src.core.task_dispatch import dispatch_command

            await dispatch_command(cmd.id)
        except Exception as exc:
            logger.warning("Failed to dispatch Taskiq task for command %s: %s", cmd.id, exc)

        return CommandActionResponse(
            command_id=cmd.id,
            idempotency_key=cmd.idempotency_key,
            action=cmd.action,
            status=cmd.status,
            created_at=cmd.created_at or datetime.now(timezone.utc),
        )

    async def get_command_status(
        self,
        command_id: uuid.UUID,
        session: AsyncSession,
    ) -> Optional[CommandStatusResponse]:
        """Fetch CommandRecord status for Short-Polling and status inspection."""
        stmt = select(CommandRecord).where(CommandRecord.id == command_id)
        cmd = (await session.execute(stmt)).scalar_one_or_none()
        if not cmd:
            return None

        return CommandStatusResponse(
            command_id=cmd.id,
            idempotency_key=cmd.idempotency_key,
            action=cmd.action,
            status=cmd.status,
            initiator=cmd.initiator,
            task_id=cmd.task_id,
            target_json=cmd.target_json or {},
            params_json=cmd.params_json or {},
            result_json=cmd.result_json,
            error_message=cmd.error_message,
            created_at=cmd.created_at or datetime.now(timezone.utc),
            updated_at=cmd.updated_at,
        )
