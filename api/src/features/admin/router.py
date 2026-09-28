"""Administrator-only API for encrypted integration credentials."""

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.db import get_db_session
from api.src.core.redis import get_redis
from api.src.core.security import AdminIdentity, require_admin_intraservice_auth

from .schemas import ServiceCredentialsStatus, ServiceCredentialsUpdate, ServiceCredentialsUpdateResponse
from .service import AdminCredentialsService

router = APIRouter(prefix="/admin", tags=["Administration"])


def get_admin_credentials_service() -> AdminCredentialsService:
    return AdminCredentialsService()


@router.get("/service-credentials/status", response_model=ServiceCredentialsStatus)
async def get_service_credentials_status(
    _admin: Annotated[AdminIdentity, Depends(require_admin_intraservice_auth)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
    service: Annotated[AdminCredentialsService, Depends(get_admin_credentials_service)],
) -> ServiceCredentialsStatus:
    return await service.get_status(session, redis_client)


@router.put("/service-credentials", response_model=ServiceCredentialsUpdateResponse)
async def update_service_credentials(
    request: ServiceCredentialsUpdate,
    _admin: Annotated[AdminIdentity, Depends(require_admin_intraservice_auth)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
    service: Annotated[AdminCredentialsService, Depends(get_admin_credentials_service)],
) -> ServiceCredentialsUpdateResponse:
    return await service.update(session, redis_client, request)
