"""Persistence and read-only synchronization for the IntraService service catalog."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.automation.service_routing import (
    CatalogValidationState,
    RedirectStrategy,
    ServiceCatalogEntry,
    ServiceCatalogVersion,
    ServiceRouteBinding,
    canonical_catalog_hash,
)
from core.database.models import (
    ServiceCatalogEntryRecord,
    ServiceCatalogVersionRecord,
    ServiceRouteBindingRecord,
)
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import ServiceDTO


class ServiceCatalogRepository:
    async def current(
        self, session: AsyncSession
    ) -> tuple[ServiceCatalogVersion | None, list[ServiceCatalogEntry], list[ServiceRouteBinding]]:
        version_record = await session.scalar(
            select(ServiceCatalogVersionRecord)
            .where(
                ServiceCatalogVersionRecord.is_active.is_(True),
                ServiceCatalogVersionRecord.validation_state == CatalogValidationState.validated.value,
            )
            .order_by(ServiceCatalogVersionRecord.version.desc())
            .limit(1)
        )
        if version_record is None:
            return None, [], []
        entry_records = list(
            (
                await session.scalars(
                    select(ServiceCatalogEntryRecord)
                    .where(ServiceCatalogEntryRecord.catalog_version_id == version_record.id)
                    .order_by(ServiceCatalogEntryRecord.service_id)
                )
            ).all()
        )
        binding_records = list(
            (
                await session.scalars(
                    select(ServiceRouteBindingRecord)
                    .where(ServiceRouteBindingRecord.catalog_hash == version_record.catalog_hash)
                    .order_by(ServiceRouteBindingRecord.key, ServiceRouteBindingRecord.version)
                )
            ).all()
        )
        version = ServiceCatalogVersion(
            id=version_record.id,
            version=version_record.version,
            catalog_hash=version_record.catalog_hash,
            fetched_at=version_record.fetched_at,
            source=version_record.source,
            validation_state=version_record.validation_state,
            is_active=version_record.is_active,
        )
        entries = [
            ServiceCatalogEntry(
                service_id=item.service_id,
                service_path=item.service_path,
                parent_service_id=item.parent_service_id,
                task_type_id=item.task_type_id,
                is_active=item.is_active,
                form_metadata=item.form_metadata_json,
                field_metadata=item.field_metadata_json,
                catalog_hash=item.catalog_hash,
            )
            for item in entry_records
        ]
        bindings = [
            ServiceRouteBinding(
                key=item.key,
                version=item.version,
                service_ids=tuple(item.service_ids_json),
                allowed_case_types=tuple(item.allowed_case_types_json),
                default_case_type=item.default_case_type,
                allowed_workflows=tuple(item.allowed_workflows_json),
                allowed_capabilities=tuple(item.allowed_capabilities_json),
                required_task_type_id=item.required_task_type_id,
                required_fields=tuple(item.required_fields_json),
                redirect_strategy=item.redirect_strategy,
                risk=item.risk,
                is_active=item.is_active,
                is_validated=item.is_validated,
                catalog_hash=item.catalog_hash,
            )
            for item in binding_records
        ]
        return version, entries, bindings

    async def sync(
        self,
        session: AsyncSession,
        *,
        client: IntraServiceClient,
        auth_b64: str,
    ) -> ServiceCatalogVersion:
        services = await client.get_services(auth_b64=auth_b64)
        if not services:
            raise ValueError("service_catalog_empty_response")
        enriched_services: list[ServiceDTO] = []
        for service in services:
            if service.task_type_id is not None and not service.fields:
                task_type = await client.get_task_type(
                    task_type_id=service.task_type_id,
                    service_id=service.id,
                    auth_b64=auth_b64,
                )
                service = service.model_copy(
                    update={
                        "fields": task_type.fields,
                        "form_metadata": {
                            **service.form_metadata,
                            "task_type_name": task_type.name,
                        },
                    }
                )
            enriched_services.append(service)
        services = enriched_services
        entries = self._entries(services)
        catalog_hash = canonical_catalog_hash(entries)
        existing = await session.scalar(
            select(ServiceCatalogVersionRecord).where(ServiceCatalogVersionRecord.catalog_hash == catalog_hash)
        )
        if existing is not None:
            if existing.validation_state != CatalogValidationState.validated.value:
                raise ValueError("existing_catalog_version_not_validated")
            await session.execute(update(ServiceCatalogVersionRecord).values(is_active=False))
            existing.is_active = True
            await self._seed_unconfirmed_ad_binding(session, catalog_hash)
            await session.flush()
            return ServiceCatalogVersion(
                id=existing.id,
                version=existing.version,
                catalog_hash=existing.catalog_hash,
                fetched_at=existing.fetched_at,
                source=existing.source,
                validation_state=existing.validation_state,
                is_active=True,
            )
        next_version = int((await session.scalar(select(func.max(ServiceCatalogVersionRecord.version)))) or 0) + 1
        version = ServiceCatalogVersionRecord(
            version=next_version,
            catalog_hash=catalog_hash,
            fetched_at=datetime.now(UTC),
            source="intraservice_api",
            validation_state=CatalogValidationState.validated.value,
            is_active=False,
        )
        session.add(version)
        await session.flush()
        for entry in entries:
            session.add(
                ServiceCatalogEntryRecord(
                    catalog_version_id=version.id,
                    service_id=entry.service_id,
                    service_path=entry.service_path,
                    parent_service_id=entry.parent_service_id,
                    task_type_id=entry.task_type_id,
                    is_active=entry.is_active,
                    form_metadata_json=entry.form_metadata,
                    field_metadata_json=entry.field_metadata,
                    catalog_hash=catalog_hash,
                )
            )
        await session.flush()
        await session.execute(update(ServiceCatalogVersionRecord).values(is_active=False))
        version.is_active = True
        await self._seed_unconfirmed_ad_binding(session, catalog_hash)
        await session.flush()
        return ServiceCatalogVersion(
            id=version.id,
            version=version.version,
            catalog_hash=catalog_hash,
            fetched_at=version.fetched_at,
            source=version.source,
            validation_state=CatalogValidationState.validated,
            is_active=True,
        )

    async def validate_and_store_binding(
        self, session: AsyncSession, binding: ServiceRouteBinding
    ) -> ServiceRouteBinding:
        version, entries, _ = await self.current(session)
        if version is None or version.catalog_hash != binding.catalog_hash:
            raise ValueError("service_catalog_changed")
        by_id = {entry.service_id: entry for entry in entries}
        if not binding.service_ids:
            raise ValueError("service_binding_has_no_services")
        for service_id in binding.service_ids:
            entry = by_id.get(service_id)
            if entry is None or not entry.is_active:
                raise ValueError("service_binding_unknown_or_inactive_service")
            if binding.required_task_type_id is not None and entry.task_type_id != binding.required_task_type_id:
                raise ValueError("task_type_not_authorized")
            available_fields = {
                str(field.get("key") or field.get("id") or field.get("Id")) for field in entry.field_metadata
            }
            if not set(binding.required_fields).issubset(available_fields):
                raise ValueError("ad_required_form_fields_missing")
        validated = binding.model_copy(update={"is_validated": True, "is_active": True})
        existing = await session.scalar(
            select(ServiceRouteBindingRecord).where(
                ServiceRouteBindingRecord.key == binding.key,
                ServiceRouteBindingRecord.version == binding.version,
                ServiceRouteBindingRecord.catalog_hash == binding.catalog_hash,
            )
        )
        values = {
            "service_ids_json": list(validated.service_ids),
            "allowed_case_types_json": list(validated.allowed_case_types),
            "default_case_type": validated.default_case_type,
            "allowed_workflows_json": list(validated.allowed_workflows),
            "allowed_capabilities_json": list(validated.allowed_capabilities),
            "required_task_type_id": validated.required_task_type_id,
            "required_fields_json": list(validated.required_fields),
            "redirect_strategy": validated.redirect_strategy.value,
            "risk": validated.risk,
            "is_active": True,
            "is_validated": True,
        }
        if existing is None:
            session.add(
                ServiceRouteBindingRecord(
                    key=validated.key,
                    version=validated.version,
                    catalog_hash=validated.catalog_hash,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        await session.flush()
        return validated

    @staticmethod
    def _entries(services: list[ServiceDTO]) -> list[ServiceCatalogEntry]:
        by_id = {service.id: service for service in services}

        def path_for(service: ServiceDTO) -> str:
            parts: list[str] = []
            seen: set[int] = set()
            current: ServiceDTO | None = service
            while current is not None:
                if current.id in seen:
                    raise ValueError("service_catalog_parent_cycle")
                seen.add(current.id)
                parts.append(current.name.strip())
                current = by_id.get(current.parent_id) if current.parent_id else None
            return " → ".join(reversed([part for part in parts if part]))

        return [
            ServiceCatalogEntry(
                service_id=service.id,
                service_path=path_for(service),
                parent_service_id=service.parent_id,
                task_type_id=service.task_type_id,
                is_active=service.is_active,
                form_metadata=service.form_metadata,
                field_metadata=service.fields,
            )
            for service in services
        ]

    @staticmethod
    async def _seed_unconfirmed_ad_binding(session: AsyncSession, catalog_hash: str) -> None:
        """Create the required binding contract without guessing an AD service ID.

        Operations can activate a new version only after catalog-backed confirmation.
        Existing service IDs 55 and 232 are deliberately not used: project evidence
        identifies them as Directum services, not the network-account service.
        """
        exists = await session.scalar(
            select(ServiceRouteBindingRecord.id).where(
                ServiceRouteBindingRecord.key == "ad_account_creation",
                ServiceRouteBindingRecord.catalog_hash == catalog_hash,
            )
        )
        if exists:
            return
        session.add(
            ServiceRouteBindingRecord(
                key="ad_account_creation",
                version="1.0.0",
                service_ids_json=[],
                allowed_case_types_json=["employee_onboarding"],
                default_case_type="employee_onboarding",
                allowed_workflows_json=["employee_onboarding_workflow"],
                allowed_capabilities_json=["create_ad_user"],
                required_task_type_id=None,
                required_fields_json=[],
                redirect_strategy=RedirectStrategy.cancel_and_recreate.value,
                risk="high",
                is_active=False,
                is_validated=False,
                catalog_hash=catalog_hash,
            )
        )
