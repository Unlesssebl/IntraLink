"""Runtime policy and health gates are independent from semantic routing."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.automation.policy import CapabilityHealthService, WorkflowPolicyService
from core.database.base import Base
from core.database.models import CapabilityHealthRecord, WorkflowPolicyRecord


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_workflow_defaults_to_assisted_and_rejects_other_modes(session_factory) -> None:
    service = WorkflowPolicyService()
    async with session_factory() as session, session.begin():
        await service.require_assisted(session, workflow_key="printing_incident_workflow")
        record = await session.get(WorkflowPolicyRecord, "printing_incident_workflow")
        assert record is not None and record.mode == "ASSISTED"
        record.mode = "DISABLED"

    async with session_factory() as session:
        with pytest.raises(ValueError, match="workflow_not_assisted:DISABLED"):
            await service.require_assisted(session, workflow_key="printing_incident_workflow")


async def test_capability_circuit_breaks_and_verified_success_resets_it(session_factory) -> None:
    service = CapabilityHealthService(failure_threshold=2)
    async with session_factory() as session, session.begin():
        await service.record_failure(
            session,
            capability_key="reset_print_spooler",
            outcome="failed",
            error_code="winrm_unavailable",
        )
        await service.require_available(session, capability_key="reset_print_spooler")
        await service.record_failure(
            session,
            capability_key="reset_print_spooler",
            outcome="unknown_outcome",
            error_code="lease_lost",
        )

    async with session_factory() as session, session.begin():
        with pytest.raises(ValueError, match="capability_circuit_broken"):
            await service.require_available(session, capability_key="reset_print_spooler")
        await service.record_success(session, capability_key="reset_print_spooler")

    async with session_factory() as session:
        await service.require_available(session, capability_key="reset_print_spooler")
        record = await session.get(CapabilityHealthRecord, "reset_print_spooler")
        assert record is not None
        assert record.consecutive_failures == 0
        assert record.is_circuit_broken is False
