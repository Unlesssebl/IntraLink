"""Compatibility endpoints for the retired Telegram password flow."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import TelegramLink, User, get_db
from app.routers.deps import require_service_scope
from app.services.identity import PrincipalContext, record_security_event

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
    dependencies=[Depends(require_service_scope("telegram:link"))],
)


@router.post("/login", status_code=status.HTTP_410_GONE)
async def retired_password_login():
    raise HTTPException(
        status.HTTP_410_GONE,
        "Password login in Telegram was retired; use a one-time link code from the operator panel",
    )


@router.delete("/logout", status_code=status.HTTP_200_OK)
async def logout(
    tg_user_id: int,
    context: PrincipalContext = Depends(require_service_scope("telegram:link")),
    db: AsyncSession = Depends(get_db),
):
    link = await db.get(TelegramLink, tg_user_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Telegram identity is not linked")
    link.status = "revoked"
    link.revoked_at = dt.datetime.now(dt.timezone.utc)
    await db.execute(delete(User).where(User.tg_user_id == tg_user_id))
    await record_security_event(
        db,
        event_type="telegram_link.revoked",
        outcome="success",
        context=context,
        principal_id=link.principal_id,
        resource_type="telegram_identity",
        resource_id=str(tg_user_id),
    )
    await db.commit()
    return {"status": "success", "message": "Telegram identity unlinked"}
