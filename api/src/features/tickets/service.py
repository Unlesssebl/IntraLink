"""Business logic and IntraService orchestration for Tickets feature slice."""

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.config import settings
from core.database.models import CommandRecord
from core.intraservice import IntraServiceClient, TaskDTO, TaskLifetimeEventDTO

from .schemas import (
    AddCommentRequest,
    CommandActionResponse,
    ExecuteTicketActionRequest,
    TicketDetailDTO,
    TicketSummaryDTO,
    UpdateTicketRequest,
)


class TicketService:
    """Service handling ticket read and write operations."""

    def __init__(self, client: Optional[IntraServiceClient] = None) -> None:
        self.client = client or IntraServiceClient(
            base_url=settings.INTRASERVICE_URL,
            verify_ssl=not settings.DEBUG,
        )

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
        return [
            TicketSummaryDTO(
                id=t.id,
                name=t.name,
                service_id=t.service_id,
                service_name=t.service_name,
                status_id=t.status_id,
                status_name=t.status_name,
                priority_name=t.priority_name,
                created=t.created,
                applicant_name=t.applicant_name,
                pc_name=t.entities.pc_name if t.entities else None,
            )
            for t in tasks
        ]

    async def get_ticket(self, ticket_id: int, auth_b64: Optional[str] = None) -> TicketDetailDTO:
        task: TaskDTO = await self.client.get_task(ticket_id=ticket_id, auth_b64=auth_b64)
        return TicketDetailDTO(
            id=task.id,
            name=task.name,
            description=task.description,
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

    async def get_ticket_lifetime(self, ticket_id: int, auth_b64: Optional[str] = None) -> List[Dict[str, Any]]:
        events: List[TaskLifetimeEventDTO] = await self.client.get_task_lifetime(ticket_id=ticket_id, auth_b64=auth_b64)
        return [e.model_dump() for e in events]

    async def update_ticket(
        self,
        ticket_id: int,
        req: UpdateTicketRequest,
        auth_b64: Optional[str] = None,
    ) -> bool:
        return await self.client.update_task(
            task_id=ticket_id,
            status_id=req.status_id,
            comment=req.comment,
            executor_ids=req.executor_ids,
            is_private=req.is_private,
            auth_b64=auth_b64,
        )

    async def add_comment(
        self,
        ticket_id: int,
        req: AddCommentRequest,
        auth_b64: Optional[str] = None,
    ) -> bool:
        return await self.client.add_task_comment(
            task_id=ticket_id,
            comment=req.comment,
            is_private=req.is_private,
            auth_b64=auth_b64,
        )

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

        return CommandActionResponse(
            command_id=cmd.id,
            idempotency_key=cmd.idempotency_key,
            action=cmd.action,
            status=cmd.status,
            created_at=cmd.created_at,
        )
