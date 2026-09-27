"""Runtime workflow policy and capability health gates for ADR 0006."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import CapabilityHealthRecord, WorkflowPolicyRecord


class WorkflowPolicyService:
    """Enforce the clean-cutover execution policy at the approval boundary."""

    ASSISTED = "ASSISTED"

    async def require_assisted(self, session: AsyncSession, *, workflow_key: str) -> None:
        record = await session.scalar(
            select(WorkflowPolicyRecord)
            .where(WorkflowPolicyRecord.workflow_key == workflow_key)
            .with_for_update()
        )
        if record is None:
            record = WorkflowPolicyRecord(
                workflow_key=workflow_key,
                mode=self.ASSISTED,
                description="ADR 0006 default: explicit operator approval is required",
            )
            session.add(record)
            await session.flush()
        if record.mode != self.ASSISTED:
            raise ValueError(f"workflow_not_assisted:{record.mode}")


class CapabilityHealthService:
    """Persistent capability-scoped circuit breaker.

    Business routing never changes this state. Only technical execution outcomes
    affect it, and a successful verified execution resets the failure counter.
    """

    def __init__(self, *, failure_threshold: int = 3) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        self.failure_threshold = failure_threshold

    async def require_available(self, session: AsyncSession, *, capability_key: str) -> None:
        record = await session.get(CapabilityHealthRecord, capability_key)
        if record is not None and record.is_circuit_broken:
            raise ValueError("capability_circuit_broken")

    async def record_success(self, session: AsyncSession, *, capability_key: str) -> None:
        record = await self._locked_record(session, capability_key)
        record.consecutive_failures = 0
        record.is_circuit_broken = False
        record.details_json = {"last_outcome": "succeeded"}

    async def record_failure(
        self,
        session: AsyncSession,
        *,
        capability_key: str,
        outcome: str,
        error_code: str | None,
    ) -> None:
        record = await self._locked_record(session, capability_key)
        failures = record.consecutive_failures + 1
        record.consecutive_failures = failures
        record.last_failure_at = datetime.now(UTC)
        record.is_circuit_broken = failures >= self.failure_threshold
        record.details_json = {
            "last_outcome": outcome,
            "last_error_code": error_code,
            "failure_threshold": self.failure_threshold,
        }

    @staticmethod
    async def _locked_record(
        session: AsyncSession, capability_key: str
    ) -> CapabilityHealthRecord:
        record = await session.scalar(
            select(CapabilityHealthRecord)
            .where(CapabilityHealthRecord.capability_key == capability_key)
            .with_for_update()
        )
        if record is None:
            record = CapabilityHealthRecord(capability_key=capability_key)
            session.add(record)
            await session.flush()
        return record
