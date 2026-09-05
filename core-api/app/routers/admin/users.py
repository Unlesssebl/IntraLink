"""Administration of verified Telegram identity links."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import Principal, TelegramLink, User, get_db
from app.routers.deps import require_permission
from app.services.identity import PrincipalContext, record_security_event

router = APIRouter()


class AddUserRequest(BaseModel):
    telegram_id: int
    username: str | None = None
    full_name: str | None = None


@router.get("/admin/api/users")
async def get_telegram_users(
    _context: PrincipalContext = Depends(require_permission("identity:manage")),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(TelegramLink, Principal)
        .join(Principal, Principal.id == TelegramLink.principal_id)
        .order_by(Principal.display_name)
    )).all()
    return {"users": [
        {
            "tg_user_id": link.tg_user_id,
            "username": principal.subject,
            "full_name": principal.display_name,
            "is_active": link.status == "verified" and principal.status == "active",
            "status": link.status,
            "verified_at": link.verified_at,
        }
        for link, principal in rows
    ]}


@router.post("/admin/api/users/add", status_code=status.HTTP_410_GONE)
async def add_telegram_user_retired(
    _payload: AddUserRequest,
    _context: PrincipalContext = Depends(require_permission("identity:manage")),
):
    raise HTTPException(
        status.HTTP_410_GONE,
        "Manual Telegram allow-listing was retired; the operator must use a one-time link code",
    )


add_telegram_user = add_telegram_user_retired


@router.post("/admin/api/users/{tg_user_id}/toggle")
async def toggle_telegram_user(
    tg_user_id: int,
    context: PrincipalContext = Depends(require_permission("identity:manage")),
    db: AsyncSession = Depends(get_db),
):
    link = await db.get(TelegramLink, tg_user_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram link not found")
    if link.status != "verified":
        raise HTTPException(status.HTTP_409_CONFLICT, "Revoked links must be verified again")
    link.status = "revoked"
    link.revoked_at = dt.datetime.now(dt.timezone.utc)
    await record_security_event(
        db, event_type="telegram_link.revoked", outcome="success", context=context,
        resource_type="telegram_identity", resource_id=str(tg_user_id),
        details={"reason": "administrator_action"},
    )
    await db.commit()
    return {"status": "success", "telegram_id": tg_user_id, "is_active": False}


@router.delete("/admin/api/users/{tg_user_id}")
async def delete_telegram_user(
    tg_user_id: int,
    context: PrincipalContext = Depends(require_permission("identity:manage")),
    db: AsyncSession = Depends(get_db),
):
    link = await db.get(TelegramLink, tg_user_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram link not found")
    principal_id = link.principal_id
    await db.delete(link)
    user = await db.get(User, tg_user_id)
    if user:
        await db.delete(user)
    await record_security_event(
        db, event_type="telegram_link.deleted", outcome="success", context=context,
        principal_id=principal_id, resource_type="telegram_identity", resource_id=str(tg_user_id),
    )
    await db.commit()
    return {"status": "success", "telegram_id": tg_user_id}
