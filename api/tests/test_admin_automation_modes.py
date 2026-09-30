"""Integration tests for admin workflow execution mode management (ADR 0006)."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.src.features.admin.schemas import UpdateWorkflowExecutionModeRequest
from api.src.features.admin.service import AdminCredentialsService
from core.database.base import Base
from core.database.models import (
    WorkflowExecutionModeEventRecord,
    WorkflowExecutionModeRecord,
)


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_workflow_execution_modes_defaults_all_to_assisted(session_factory) -> None:
    service = AdminCredentialsService()
    async with session_factory() as session:
        modes = await service.get_workflow_execution_modes(session)

    assert len(modes) > 0
    for mode in modes:
        assert mode.desired_mode == "ASSISTED"
        if mode.workflow_key == "wireless_access_workflow":
            assert mode.auto_execution_eligible is True
        else:
            assert mode.auto_execution_eligible is False


@pytest.mark.asyncio
async def test_update_workflow_mode_rejects_ineligible_for_full_auto(session_factory) -> None:
    service = AdminCredentialsService()
    redis_mock = AsyncMock()

    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await service.update_workflow_execution_mode(
                session=session,
                redis_client=redis_mock,
                workflow_key="employee_onboarding_workflow",
                request=UpdateWorkflowExecutionModeRequest(
                    mode="FULL_AUTO",
                    expected_revision=1,
                    reason="Attempting to enable full auto on onboarding",
                ),
                admin_login="admin.user",
            )
        assert exc.value.status_code == 400
        assert "workflow_not_eligible_for_full_auto" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_workflow_mode_enforces_revision_locking(session_factory) -> None:
    service = AdminCredentialsService()
    redis_mock = AsyncMock()

    async with session_factory() as session:
        with pytest.raises(HTTPException) as exc:
            await service.update_workflow_execution_mode(
                session=session,
                redis_client=redis_mock,
                workflow_key="wireless_access_workflow",
                request=UpdateWorkflowExecutionModeRequest(
                    mode="FULL_AUTO",
                    expected_revision=999,  # Stale revision
                    reason="Attempt with stale revision",
                ),
                admin_login="admin.user",
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_update_workflow_mode_success_and_auditing(session_factory) -> None:
    service = AdminCredentialsService()
    redis_mock = AsyncMock()

    async with session_factory() as session:
        updated = await service.update_workflow_execution_mode(
            session=session,
            redis_client=redis_mock,
            workflow_key="wireless_access_workflow",
            request=UpdateWorkflowExecutionModeRequest(
                mode="FULL_AUTO",
                expected_revision=1,
                reason="Enabling full auto for tested wireless access",
            ),
            admin_login="secadmin",
        )
        assert updated.desired_mode == "FULL_AUTO"
        assert updated.policy_revision == 2
        assert updated.updated_by == "secadmin"

    async with session_factory() as session:
        rec = await session.scalar(
            select(WorkflowExecutionModeRecord).where(
                WorkflowExecutionModeRecord.workflow_key == "wireless_access_workflow"
            )
        )
        assert rec is not None
        assert rec.desired_mode == "FULL_AUTO"
        assert rec.revision == 2

        event = await session.scalar(
            select(WorkflowExecutionModeEventRecord).where(
                WorkflowExecutionModeEventRecord.workflow_key == "wireless_access_workflow"
            )
        )
        assert event is not None
        assert event.previous_mode == "ASSISTED"
        assert event.new_mode == "FULL_AUTO"
        assert event.revision == 2
        assert event.reason == "Enabling full auto for tested wireless access"
        assert event.changed_by == "secadmin"

    redis_mock.publish.assert_awaited_once_with(
        "workflow_mode_changed", "wireless_access_workflow:FULL_AUTO:2"
    )
