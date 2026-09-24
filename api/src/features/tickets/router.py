"""Tickets feature router."""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.db import get_db_session
from api.src.core.security import get_intraservice_auth
from core.intraservice.exceptions import IntraServiceNotFoundError

from .schemas import (
    AddCommentRequest,
    CommandActionResponse,
    CommandStatusResponse,
    ExecuteTicketActionRequest,
    TicketDetailDTO,
    TicketSummaryDTO,
    UpdateTicketRequest,
)
from .service import TicketService

router = APIRouter(prefix="/tickets", tags=["Tickets"])
tasks_router = APIRouter(prefix="/tasks", tags=["Tasks"])


def get_ticket_service() -> TicketService:
    return TicketService()


@router.get("", response_model=List[TicketSummaryDTO])
@router.get("/", response_model=List[TicketSummaryDTO], include_in_schema=False)
async def list_tickets(
    filter_id: int = Query(default=984, description="IntraService filter ID (e.g. 984 for 1st line queue)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TicketService = Depends(get_ticket_service),
) -> List[TicketSummaryDTO]:
    """Retrieve ticket list matching specified filter ID."""
    return await service.list_tickets(filter_id=filter_id, page=page, page_size=page_size, auth_b64=auth_b64)


@router.get("/services/catalog", response_model=List[Dict[str, Any]])
async def get_services_catalog(
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TicketService = Depends(get_ticket_service),
) -> List[Dict[str, Any]]:
    """Retrieve service catalog for routing and redirects."""
    services = await service.client.get_services(auth_b64=auth_b64)
    return [s.model_dump() for s in services]


@router.get("/{ticket_id}", response_model=TicketDetailDTO)
async def get_ticket(
    ticket_id: int,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TicketService = Depends(get_ticket_service),
) -> TicketDetailDTO:
    """Retrieve full details of a single ticket."""
    try:
        return await service.get_ticket(ticket_id=ticket_id, auth_b64=auth_b64)
    except IntraServiceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket #{ticket_id} not found in IntraService",
        ) from exc


@router.get("/{ticket_id}/lifetime", response_model=List[Dict[str, Any]])
async def get_ticket_lifetime(
    ticket_id: int,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TicketService = Depends(get_ticket_service),
) -> List[Dict[str, Any]]:
    """Retrieve change history and comments of a ticket."""
    return await service.get_ticket_lifetime(ticket_id=ticket_id, auth_b64=auth_b64)


@router.patch("/{ticket_id}")
async def update_ticket(
    ticket_id: int,
    req: UpdateTicketRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TicketService = Depends(get_ticket_service),
) -> dict:
    """Update ticket status, comment, or assignees."""
    success = await service.update_ticket(ticket_id=ticket_id, req=req, auth_b64=auth_b64)
    return {"ticket_id": ticket_id, "updated": success}


@router.post("/{ticket_id}/comments")
async def add_comment(
    ticket_id: int,
    req: AddCommentRequest,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TicketService = Depends(get_ticket_service),
) -> dict:
    """Post a comment to a ticket."""
    success = await service.add_comment(ticket_id=ticket_id, req=req, auth_b64=auth_b64)
    return {"ticket_id": ticket_id, "comment_added": success}


@router.post("/{ticket_id}/actions", response_model=CommandActionResponse)
async def execute_ticket_action(
    ticket_id: int,
    req: ExecuteTicketActionRequest,
    service: TicketService = Depends(get_ticket_service),
    db: AsyncSession = Depends(get_db_session),
) -> CommandActionResponse:
    """Enqueue an idempotent command for this ticket (Outbox pattern)."""
    return await service.execute_action(
        ticket_id=ticket_id,
        req=req,
        initiator="web-user",
        session=db,
    )


@tasks_router.get("/{command_id}", response_model=CommandStatusResponse)
async def get_task_status(
    command_id: uuid.UUID,
    service: TicketService = Depends(get_ticket_service),
    db: AsyncSession = Depends(get_db_session),
) -> CommandStatusResponse:
    """Retrieve command execution status (pending | running | succeeded | failed) for Short-Polling."""
    status_dto = await service.get_command_status(command_id=command_id, session=db)
    if not status_dto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Command '{command_id}' not found",
        )
    return status_dto


@router.get("/tasks/{command_id}", response_model=CommandStatusResponse)
async def get_ticket_task_status(
    command_id: uuid.UUID,
    service: TicketService = Depends(get_ticket_service),
    db: AsyncSession = Depends(get_db_session),
) -> CommandStatusResponse:
    """Alias for command status polling under tickets router."""
    return await get_task_status(command_id=command_id, service=service, db=db)


@router.get("/{ticket_id}/attachments/{file_id}")
async def download_attachment(
    ticket_id: int,
    file_id: int,
    auth_b64: Optional[str] = Depends(get_intraservice_auth),
    service: TicketService = Depends(get_ticket_service),
) -> Response:
    """Download binary content of an attachment."""
    content = await service.client.download_attachment(file_id=file_id, auth_b64=auth_b64)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attachment #{file_id} not found or empty",
        )
    return Response(content=content, media_type="application/octet-stream")
