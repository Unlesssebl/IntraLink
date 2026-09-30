"""Secure management of IntraService and Active Directory credentials."""

from datetime import UTC, datetime
from typing import Any

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
from core.ad.pool import ActiveDirectoryPool
from core.ad.provisioning import DEFAULT_AD_DELIVERY_POLICY
from core.automation.capabilities import CapabilityRisk, get_default_capability_registry
from core.automation.execution_policy import is_deployment_force_disabled
from core.automation.policy import CapabilityHealthService
from core.automation.service_catalog import ServiceCatalogRepository
from core.automation.service_routing import RedirectStrategy, ServiceRouteBinding
from core.automation.worker_attestation import WorkerAttestationService
from core.crypto import get_fernet
from core.database.models import (
    CapabilityExecutionPolicyRecord,
    ExecutionPolicyEventRecord,
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
    CapabilityExecutionPolicyStatusDTO,
    CatalogStatus,
    ServiceCredentialsStatus,
    ServiceCredentialsUpdate,
    ServiceCredentialsUpdateResponse,
    UpdateCapabilityExecutionPolicyRequest,
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
            verified_server, ou_count = await ActiveDirectoryPool.probe_credentials(
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
            delivery_policy=DEFAULT_AD_DELIVERY_POLICY,
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

    async def get_execution_policies(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis,
    ) -> list[CapabilityExecutionPolicyStatusDTO]:
        registry = get_default_capability_registry()
        mutating_caps = [
            spec for spec in registry.list_all()
            if spec.is_mutating
        ]

        pol_rows = (await session.scalars(select(CapabilityExecutionPolicyRecord))).all()
        policies_by_key = {p.capability_key: p for p in pol_rows}

        binding_rows = (
            await session.scalars(
                select(ServiceRouteBindingRecord).where(
                    ServiceRouteBindingRecord.is_active.is_(True),
                    ServiceRouteBindingRecord.is_validated.is_(True),
                )
            )
        ).all()
        active_binding_cap_keys = {
            cap
            for b in binding_rows
            for cap in (b.allowed_capabilities_json or [])
        }

        attestation_service = WorkerAttestationService()
        health_service = CapabilityHealthService()
        ceiling_enabled = not is_deployment_force_disabled()

        items: list[CapabilityExecutionPolicyStatusDTO] = []
        for spec in mutating_caps:
            cap_key = spec.key
            pol = policies_by_key.get(cap_key)

            circuit_closed = await health_service.is_circuit_closed(session, cap_key)
            circuit_state = "closed" if circuit_closed else "open"

            worker_att = await attestation_service.verify_worker_for_capability(redis_client, cap_key)
            worker_available = worker_att.healthy

            requires_binding = cap_key in ("create_ad_user", "disable_ad_user", "add_wlan_group_member")
            if requires_binding:
                binding_state = "valid" if cap_key in active_binding_cap_keys else "invalid"
            else:
                binding_state = "not_required"

            desired_state = pol.desired_state if pol else "disabled"
            revision = pol.revision if pol else 0
            reason = pol.reason if pol else "Политика не создана"
            changed_by = pol.changed_by if pol else "system"
            changed_at = pol.changed_at if pol else datetime.now(UTC)

            reasons: list[str] = []
            if pol is None:
                reasons.append("policy_not_found")
            elif pol.desired_state != "enabled":
                reasons.append("policy_disabled")

            if not ceiling_enabled:
                reasons.append("deployment_force_disabled")

            if circuit_state != "closed":
                reasons.append("circuit_breaker_open")

            if requires_binding and binding_state != "valid":
                reasons.append("missing_service_binding")

            if not worker_available:
                reasons.extend(worker_att.reasons)

            reasons = sorted(set(reasons))
            effective_state = "allowed" if not reasons else "blocked"

            items.append(
                CapabilityExecutionPolicyStatusDTO(
                    capability_key=cap_key,
                    name=spec.name,
                    description=spec.description,
                    risk_level=spec.risk.value,
                    requires_binding=requires_binding,
                    desired_state=desired_state,
                    revision=revision,
                    reason=reason,
                    changed_by=changed_by,
                    changed_at=changed_at,
                    deployment_ceiling=ceiling_enabled,
                    binding_state=binding_state,
                    circuit_state=circuit_state,
                    worker_available=worker_available,
                    effective_state=effective_state,
                    reason_codes=reasons,
                )
            )

        return sorted(items, key=lambda x: x.capability_key)

    async def update_execution_policy(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis,
        capability_key: str,
        request: UpdateCapabilityExecutionPolicyRequest,
        admin_login: str,
    ) -> CapabilityExecutionPolicyStatusDTO:
        registry = get_default_capability_registry()
        spec = registry.get(capability_key)
        if spec is None or not spec.is_mutating:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Мутирующая возможность '{capability_key}' не найдена в реестре",
            )


        stmt = (
            select(CapabilityExecutionPolicyRecord)
            .where(CapabilityExecutionPolicyRecord.capability_key == capability_key)
            .with_for_update()
        )
        row = await session.scalar(stmt)
        if row is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Политика выполнения для '{capability_key}' не найдена в базе данных",
            )

        if row.revision != request.expected_revision:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Конфликт версий: ревизия политики {row.revision} не совпадает с ожидаемой {request.expected_revision}",
            )

        old_state = row.desired_state
        new_revision = row.revision + 1
        now = datetime.now(UTC)

        row.desired_state = request.desired_state
        row.revision = new_revision
        row.reason = request.reason.strip()
        row.changed_by = admin_login
        row.changed_at = now

        event = ExecutionPolicyEventRecord(
            capability_key=capability_key,
            previous_state=old_state,
            new_state=request.desired_state,
            revision=new_revision,
            reason=request.reason.strip(),
            changed_by=admin_login,
            created_at=now,
        )
        session.add(event)
        await session.commit()

        all_policies = await self.get_execution_policies(session, redis_client)
        for item in all_policies:
            if item.capability_key == capability_key:
                return item

        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Не удалось получить обновленный статус политики",
        )

