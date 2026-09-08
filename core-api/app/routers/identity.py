"""Identity, role, credential and security-audit administration."""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import (
    Principal,
    PrincipalRole,
    SecurityEvent,
    ServiceCredential,
    get_db,
)
from app.routers.deps import require_permission, require_service_scope
from app.services.identity import (
    PrincipalContext,
    create_service_credential,
    create_telegram_link_code,
    consume_telegram_link_code,
    record_security_event,
)

router = APIRouter(prefix="/api/v2/identities", tags=["Identity v2"])

ALLOWED_SERVICE_SCOPES = frozenset({
    "task:read", "task:mutate", "triage:read", "triage:mutate", "ai:use",
    "command:read", "command:create", "command:cancel", "events:read",
    "command:claim:windows", "command:finish:windows",
    "command:claim:backend", "command:finish:backend",
    "telegram:challenge:issue", "telegram:challenge:consume", "telegram:link",
})
ALLOWED_ROLES = frozenset({"helpdesk_operator", "system_admin", "security_auditor"})


class ServiceCredentialRequest(BaseModel):
    subject: str = Field(min_length=3, max_length=160)
    display_name: str = Field(min_length=3, max_length=200)
    scopes: set[str] = Field(min_length=1)
    expires_in_days: int | None = Field(90, ge=1, le=365)
    reason: str = Field(min_length=3, max_length=1000)


class RoleRequest(BaseModel):
    role: str
    reason: str = Field(min_length=3, max_length=1000)


class TelegramLinkRequest(BaseModel):
    code: str = Field(min_length=8, max_length=100)
    tg_user_id: int


@router.get("")
async def list_principals(
    _context: PrincipalContext = Depends(require_permission("identity:manage")),
    db: AsyncSession = Depends(get_db),
):
    principals = list((await db.scalars(select(Principal).order_by(Principal.created_at.desc()))).all())
    return {"items": [
        {
            "id": str(item.id), "type": item.type, "subject": item.subject,
            "display_name": item.display_name, "status": item.status,
        }
        for item in principals
    ]}


@router.post("/service-credentials", status_code=status.HTTP_201_CREATED)
async def provision_service_credential(
    payload: ServiceCredentialRequest,
    context: PrincipalContext = Depends(require_permission("credentials:manage")),
    db: AsyncSession = Depends(get_db),
):
    unknown = payload.scopes - ALLOWED_SERVICE_SCOPES
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"unknown_scopes": sorted(unknown)})
    expires_at = (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=payload.expires_in_days)
        if payload.expires_in_days else None
    )
    principal, credential, secret = await create_service_credential(
        db,
        subject=payload.subject.strip().lower(),
        display_name=payload.display_name.strip(),
        scopes=payload.scopes,
        expires_at=expires_at,
        creator_context=context,
        reason=payload.reason,
    )
    return {
        "principal_id": str(principal.id),
        "key_id": credential.key_id,
        "secret": secret,
        "scopes": credential.scopes_json,
        "expires_at": credential.expires_at,
        "created_by": context.subject,
    }


@router.delete("/service-credentials/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_service_credential(
    key_id: str,
    reason: str = Query(..., min_length=3, max_length=1000),
    context: PrincipalContext = Depends(require_permission("credentials:manage")),
    db: AsyncSession = Depends(get_db),
):
    credential = await db.scalar(select(ServiceCredential).where(ServiceCredential.key_id == key_id))
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Credential not found")
    credential.revoked_at = dt.datetime.now(dt.timezone.utc)
    await record_security_event(
        db, event_type="service_credential.revoked", outcome="success", context=context,
        resource_type="service_credential", resource_id=key_id,
        details={"reason": reason},
    )
    await db.commit()


@router.put("/{principal_id}/roles")
async def assign_role(
    principal_id: uuid.UUID,
    payload: RoleRequest,
    context: PrincipalContext = Depends(require_permission("identity:manage")),
    db: AsyncSession = Depends(get_db),
):
    if payload.role not in ALLOWED_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown role")
    if await db.get(Principal, principal_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Principal not found")
    mapping = await db.get(PrincipalRole, (principal_id, payload.role))
    if mapping is None:
        db.add(PrincipalRole(
            principal_id=principal_id, role_name=payload.role, assigned_by=context.principal_id
        ))
        await record_security_event(
            db, event_type="role.assigned", outcome="success", context=context,
            resource_type="principal", resource_id=str(principal_id),
            details={"role": payload.role, "reason": payload.reason},
        )
        await db.commit()
    return {"principal_id": str(principal_id), "role": payload.role}


@router.delete("/{principal_id}/roles/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_role(
    principal_id: uuid.UUID,
    role: str,
    reason: str = Query(..., min_length=3, max_length=1000),
    context: PrincipalContext = Depends(require_permission("identity:manage")),
    db: AsyncSession = Depends(get_db),
):
    mapping = await db.get(PrincipalRole, (principal_id, role))
    if mapping is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role assignment not found")
    if role == "system_admin":
        admin_count = int(await db.scalar(
            select(func.count(PrincipalRole.principal_id)).where(
                PrincipalRole.role_name == "system_admin"
            )
        ) or 0)
        if admin_count <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "Cannot remove the last system administrator")
    await db.delete(mapping)
    await record_security_event(
        db, event_type="role.revoked", outcome="success", context=context,
        resource_type="principal", resource_id=str(principal_id),
        details={"role": role, "reason": reason},
    )
    await db.commit()


@router.post("/telegram/link-code")
async def issue_telegram_link_code(
    context: PrincipalContext = Depends(require_permission("identity:link:self")),
    db: AsyncSession = Depends(get_db),
):
    if context.principal_type != "human" or context.principal_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Human session required")
    code = await create_telegram_link_code(db, context.principal_id)
    return {"code": code, "expires_in": 600}


@router.post("/telegram/link")
async def link_telegram_identity(
    payload: TelegramLinkRequest,
    _service: PrincipalContext = Depends(require_service_scope("telegram:link")),
    db: AsyncSession = Depends(get_db),
):
    principal = await consume_telegram_link_code(db, code=payload.code, tg_user_id=payload.tg_user_id)
    return {"status": "verified", "principal_id": str(principal.id)}


@router.get("/security-events")
async def list_security_events(
    limit: int = Query(100, ge=1, le=500),
    _context: PrincipalContext = Depends(require_permission("audit:read")),
    db: AsyncSession = Depends(get_db),
):
    events = list((await db.scalars(
        select(SecurityEvent).order_by(desc(SecurityEvent.created_at)).limit(limit)
    )).all())
    return {"items": [
        {
            "id": str(item.id), "event_type": item.event_type, "outcome": item.outcome,
            "principal_id": str(item.principal_id) if item.principal_id else None,
            "auth_method": item.auth_method, "resource_type": item.resource_type,
            "resource_id": item.resource_id, "details": item.details_json,
            "created_at": item.created_at,
        }
        for item in events
    ]}
