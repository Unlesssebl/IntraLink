"""Secure management of IntraService service-bot credentials."""

from typing import Any

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.config import settings
from api.src.core.db import async_session_factory
from api.src.features.autopilot.service import AutomationService
from core.automation.service_catalog import ServiceCatalogRepository
from core.automation.service_routing import RedirectStrategy, ServiceRouteBinding
from core.crypto import get_fernet
from core.database.models import (
    ServiceCatalogEntryRecord,
    ServiceCatalogVersionRecord,
    ServiceRouteBindingRecord,
    SystemState,
)
from core.intraservice.auth import ServiceAuthBootstrap
from core.intraservice.client import IntraServiceClient

from .schemas import (
    ActivateAdAccountBindingRequest,
    AdAccountBindingStatus,
    CatalogStatus,
    ServiceCredentialsStatus,
    ServiceCredentialsUpdate,
    ServiceCredentialsUpdateResponse,
)


class AdminCredentialsService:
    def __init__(self) -> None:
        self.client = IntraServiceClient(base_url=settings.INTRASERVICE_URL, verify_ssl=settings.SSL_VERIFY)
        self.catalog_repository = ServiceCatalogRepository()

    async def get_ad_account_binding(self, session: AsyncSession) -> AdAccountBindingStatus:
        version, entries, bindings = await self.catalog_repository.current(session)
        entry = next((item for item in entries if item.service_id == 53 and item.is_active), None)
        binding = next((item for item in bindings if item.key == "ad_account_creation"), None)
        current = bool(version and binding and binding.catalog_hash == version.catalog_hash)
        return AdAccountBindingStatus(
            service_path=entry.service_path if entry else None,
            catalog_hash=version.catalog_hash if version else None,
            catalog_version=version.version if version else None,
            task_type_id=entry.task_type_id if entry else None,
            field_metadata=entry.field_metadata if entry else [],
            active=bool(current and binding and binding.is_active),
            validated=bool(current and binding and binding.is_validated),
            requires_confirmation=not bool(current and binding and binding.is_active and binding.is_validated),
        )

    async def activate_ad_account_binding(
        self, session: AsyncSession, request: ActivateAdAccountBindingRequest
    ) -> AdAccountBindingStatus:
        version, entries, _ = await self.catalog_repository.current(session)
        if version is None or version.catalog_hash != request.catalog_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "service_catalog_changed")
        entry = next((item for item in entries if item.service_id == 53 and item.is_active), None)
        if entry is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "service_53_not_active")
        if entry.service_path != request.confirmed_service_path:
            raise HTTPException(status.HTTP_409_CONFLICT, "service_path_confirmation_mismatch")
        binding = ServiceRouteBinding(
            key="ad_account_creation",
            version="1.0.0",
            service_ids=(53,),
            allowed_case_types=("employee_onboarding",),
            default_case_type="employee_onboarding",
            allowed_workflows=("employee_onboarding_workflow",),
            allowed_capabilities=("create_ad_user",),
            required_task_type_id=entry.task_type_id,
            required_fields=(),
            redirect_strategy=RedirectStrategy.cancel_and_recreate,
            risk="high",
            is_active=True,
            is_validated=False,
            catalog_hash=version.catalog_hash,
        )
        try:
            await self.catalog_repository.validate_and_store_binding(session, binding)
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        await session.commit()
        return await self.get_ad_account_binding(session)

    async def get_status(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis,
    ) -> ServiceCredentialsStatus:
        row = await session.scalar(
            select(SystemState).where(SystemState.key == ServiceAuthBootstrap.STATE_KEY_CREDENTIALS)
        )
        data: dict[str, Any] = row.state_data if row and row.state_data else {}

        redis_cached = False
        try:
            redis_cached = bool(await redis_client.exists(ServiceAuthBootstrap.KEY_SERVICE_AUTH_B64))
        except Exception:
            # PostgreSQL remains the durable source of truth if Redis is unavailable.
            redis_cached = False

        version = await session.scalar(
            select(ServiceCatalogVersionRecord)
            .where(
                ServiceCatalogVersionRecord.is_active.is_(True),
                ServiceCatalogVersionRecord.validation_state == "validated",
            )
            .order_by(ServiceCatalogVersionRecord.version.desc())
            .limit(1)
        )
        entry_count = 0
        binding_count = 0
        if version is not None:
            entry_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ServiceCatalogEntryRecord)
                    .where(ServiceCatalogEntryRecord.catalog_version_id == version.id)
                )
                or 0
            )
            binding_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ServiceRouteBindingRecord)
                    .where(
                        ServiceRouteBindingRecord.catalog_hash == version.catalog_hash,
                        ServiceRouteBindingRecord.is_active.is_(True),
                        ServiceRouteBindingRecord.is_validated.is_(True),
                    )
                )
                or 0
            )

        return ServiceCredentialsStatus(
            configured=bool(data.get("encrypted_token") and data.get("bot_user_id")),
            encryption_ready=get_fernet() is not None,
            login=data.get("login"),
            bot_user_id=data.get("bot_user_id"),
            updated_at=row.updated_at if row else None,
            redis_cached=redis_cached,
            catalog=CatalogStatus(
                available=version is not None,
                version=version.version if version else None,
                entries=entry_count,
                active_bindings=binding_count,
            ),
        )

    async def update(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis,
        request: ServiceCredentialsUpdate,
    ) -> ServiceCredentialsUpdateResponse:
        login = request.login.strip()
        if get_fernet() is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ENCRYPTION_KEY не настроен: сохранение секрета заблокировано",
            )
        auth_b64, bot_user_id = await self.client.verify_credentials(login, request.password)
        if not auth_b64 or bot_user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль сервисного бота")

        bootstrap = ServiceAuthBootstrap(session_factory=async_session_factory)
        await bootstrap.save_credentials(
            auth_b64=auth_b64,
            bot_user_id=bot_user_id,
            login=login,
            redis_client=redis_client,
            session_factory=async_session_factory,
        )

        sync_state = "skipped"
        sync_error: str | None = None
        if request.sync_catalog:
            try:
                await AutomationService().sync_service_catalog(session, auth_b64=auth_b64)
                sync_state = "succeeded"
            except HTTPException as exc:
                sync_state = "failed"
                sync_error = str(exc.detail)
            except Exception:
                sync_state = "failed"
                sync_error = "Каталог не синхронизирован. Учетные данные сохранены."

        status_dto = await self.get_status(session, redis_client)
        if not status_dto.configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Учетные данные проверены, но не были сохранены в долговременном хранилище",
            )
        return ServiceCredentialsUpdateResponse(
            status=status_dto,
            catalog_sync=sync_state,
            catalog_sync_error=sync_error,
        )
