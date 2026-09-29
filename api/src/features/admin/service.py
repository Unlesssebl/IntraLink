"""Secure management of IntraService and Active Directory credentials."""

import asyncio
from datetime import UTC, datetime
from typing import Any

import ldap3
import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.config import settings
from api.src.core.db import async_session_factory
from api.src.features.autopilot.service import AutomationService
from core.ad.credentials import (
    AD_CREDENTIALS_STATE_KEY,
    AD_DOMAIN,
    AD_USERS_ROOT_OU,
    ActiveDirectoryCredentialVault,
)
from core.ad.pool import ActiveDirectoryPool, ADPoolConfig
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
    AdCredentialsStatus,
    AdCredentialsUpdate,
    AdCredentialsUpdateResponse,
    CatalogStatus,
    ServiceCredentialsStatus,
    ServiceCredentialsUpdate,
    ServiceCredentialsUpdateResponse,
)


class AdminCredentialsService:
    def __init__(self) -> None:
        self.client = IntraServiceClient(base_url=settings.INTRASERVICE_URL, verify_ssl=settings.SSL_VERIFY)
        self.catalog_repository = ServiceCatalogRepository()
        self.ad_credential_vault = ActiveDirectoryCredentialVault(session_factory=async_session_factory)

    async def get_ad_credentials_status(self, session: AsyncSession) -> AdCredentialsStatus:
        row = await session.scalar(select(SystemState).where(SystemState.key == AD_CREDENTIALS_STATE_KEY))
        data: dict[str, Any] = row.state_data if row and row.state_data else {}
        verified_at: datetime | None = None
        if data.get("verified_at"):
            try:
                verified_at = datetime.fromisoformat(str(data["verified_at"]))
            except ValueError:
                verified_at = None
        return AdCredentialsStatus(
            configured=bool(data.get("encrypted_password") and data.get("username") and data.get("servers")),
            encryption_ready=get_fernet() is not None,
            domain=AD_DOMAIN,
            users_root_ou=AD_USERS_ROOT_OU,
            servers=[str(item) for item in data.get("servers", [])],
            username=data.get("username"),
            port=int(data.get("port") or 636),
            use_ssl=bool(data.get("use_ssl", True)),
            updated_at=row.updated_at if row else None,
            last_verified_at=verified_at,
            verified_server=data.get("verified_server"),
            ou_count=int(data["ou_count"]) if data.get("ou_count") is not None else None,
        )

    @staticmethod
    def _verify_ad_credentials_sync(
        *, servers: list[str], username: str, password: str, port: int, use_ssl: bool
    ) -> tuple[str, int]:
        pool = ActiveDirectoryPool(
            ADPoolConfig(
                servers=servers,
                connect_timeout=2.0,
                port=port,
                use_ssl=use_ssl,
                bind_user=username,
                bind_password=password,
                domain=AD_DOMAIN,
            )
        )
        with pool.connection_scope(auto_bind=True, read_only=True) as connection:
            connection.search(
                AD_USERS_ROOT_OU,
                "(objectClass=organizationalUnit)",
                ldap3.BASE,
                attributes=["distinguishedName"],
            )
            if len(connection.entries) != 1:
                raise RuntimeError("ad_users_root_unavailable")
            connection.search(
                AD_USERS_ROOT_OU,
                "(objectClass=organizationalUnit)",
                ldap3.SUBTREE,
                attributes=["distinguishedName"],
            )
            if not connection.entries:
                raise RuntimeError("ad_ou_catalog_empty")
            active_server = connection.server.host if connection.server else servers[0]
            return str(active_server), len(connection.entries)

    async def update_ad_credentials(
        self, session: AsyncSession, request: AdCredentialsUpdate
    ) -> AdCredentialsUpdateResponse:
        if get_fernet() is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "ENCRYPTION_KEY не настроен: сохранение секрета заблокировано",
            )
        password = request.password.get_secret_value()
        try:
            verified_server, ou_count = await asyncio.to_thread(
                self._verify_ad_credentials_sync,
                servers=request.servers,
                username=request.username,
                password=password,
                port=request.port,
                use_ssl=request.use_ssl,
            )
        except Exception as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Проверка подключения к Active Directory не пройдена",
            ) from exc

        verified_at = datetime.now(UTC)
        await self.ad_credential_vault.save(
            session,
            servers=request.servers,
            username=request.username,
            password=password,
            port=request.port,
            use_ssl=request.use_ssl,
            verified_server=verified_server,
            ou_count=ou_count,
            verified_at=verified_at,
        )
        password = ""
        await session.commit()
        return AdCredentialsUpdateResponse(status=await self.get_ad_credentials_status(session))

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
