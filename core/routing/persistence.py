"""Persistence repository and application service for Evidence-Based Routing Cascade.

Ensures decisions are stored immutably, transactional boundaries are strictly respected,
and domain invariants are guaranteed upon loading from persistence.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.database.models import RoutingDecisionRecord
from core.routing.cascade import RoutingCascade
from core.routing.contracts import RoutingDecision, TicketSnapshot
from core.routing.exceptions import RoutingPersistenceError

logger = logging.getLogger("core.routing.persistence")


class RoutingDecisionRepository:
    """Repository for inserting and querying immutable RoutingDecision records."""

    async def insert(
        self,
        decision: RoutingDecision,
        session: AsyncSession,
        snapshot: TicketSnapshot | None = None,
        duration_ms: float | None = None,
    ) -> RoutingDecisionRecord:
        """Add a RoutingDecision to the session as an immutable record."""
        raw_trace = (
            decision.verifier_trace.model_dump(mode="json")
            if decision.verifier_trace is not None
            else None
        )
        trace_json = dict(raw_trace) if isinstance(raw_trace, dict) else ({} if duration_ms is not None else None)
        if duration_ms is not None and trace_json is not None:
            trace_json["duration_ms"] = duration_ms

        record = RoutingDecisionRecord(
            id=decision.id,
            task_id=decision.task_id,
            snapshot_hash=decision.snapshot_hash,
            router_version=decision.router_version,
            prompt_version=decision.prompt_version,
            state=decision.state.value,
            selected_scenario=decision.selected_scenario,
            selected_scenario_version=decision.selected_scenario_version,
            snapshot_json=snapshot.model_dump(mode="json") if snapshot is not None else {},
            candidates_json=[c.model_dump(mode="json") for c in decision.candidates],
            evidence_json=[e.model_dump(mode="json") for e in decision.evidence],
            verifier_result_json=(
                [v.model_dump(mode="json") for v in decision.verifier_result]
                if decision.verifier_result is not None
                else None
            ),
            missing_facts_json=list(decision.missing_facts),
            degradation_reason=decision.degradation_reason,
            decision_reason_codes_json=list(decision.decision_reason_codes),
            degraded_components_json=dict(decision.degraded_components),
            verifier_trace_json=trace_json or None,
        )
        session.add(record)
        return record

    async def get_by_id(self, decision_id: UUID, session: AsyncSession) -> RoutingDecision | None:
        """Fetch a RoutingDecision by primary key UUID and reconstruct domain model."""
        stmt = select(RoutingDecisionRecord).where(RoutingDecisionRecord.id == decision_id)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return self._record_to_domain(record)

    async def get_latest_for_task(self, task_id: int, session: AsyncSession) -> RoutingDecision | None:
        """Fetch the most recent RoutingDecision for a given task ID."""
        stmt = (
            select(RoutingDecisionRecord)
            .where(RoutingDecisionRecord.task_id == task_id)
            .order_by(RoutingDecisionRecord.created_at.desc(), RoutingDecisionRecord.id.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return self._record_to_domain(record)

    def _record_to_domain(self, record: RoutingDecisionRecord) -> RoutingDecision:
        """Reconstruct and re-validate immutable domain model from database JSON payload."""
        data = {
            "id": record.id,
            "task_id": record.task_id,
            "snapshot_hash": record.snapshot_hash,
            "router_version": record.router_version,
            "prompt_version": record.prompt_version,
            "state": record.state,
            "selected_scenario": record.selected_scenario,
            "selected_scenario_version": record.selected_scenario_version,
            "candidates": record.candidates_json or [],
            "evidence": record.evidence_json or [],
            "verifier_result": record.verifier_result_json,
            "missing_facts": record.missing_facts_json or [],
            "degradation_reason": record.degradation_reason,
            "decision_reason_codes": record.decision_reason_codes_json or [],
            "degraded_components": record.degraded_components_json or {},
            "verifier_trace": record.verifier_trace_json,
            "created_at": record.created_at,
        }
        return RoutingDecision.model_validate(data)


class RoutingDecisionService:
    """Service orchestrating routing decisions and transactional persistence."""

    def __init__(
        self,
        cascade: RoutingCascade,
        repository: RoutingDecisionRepository | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._cascade = cascade
        self._repository = repository or RoutingDecisionRepository()
        self._session_factory = session_factory

    async def analyze_and_store(
        self,
        snapshot: TicketSnapshot,
        session: AsyncSession | None = None,
    ) -> RoutingDecision:
        """Execute routing cascade and atomically store immutable decision.

        - If external session provided: executes flush() without commit.
        - If session is None: manages own transaction lifecycle with atomic commit.
        - If write fails: raises RoutingPersistenceError without exposing unpersisted decision.
        """
        decision = await self._cascade.decide(snapshot)

        if session is not None:
            try:
                await self._repository.insert(decision=decision, session=session, snapshot=snapshot)
                await session.flush()
                return decision
            except Exception as exc:
                logger.error(
                    "Failed to persist routing decision for task #%d with external session: %s",
                    snapshot.task_id,
                    type(exc).__name__,
                )
                raise RoutingPersistenceError("routing_persistence_failed") from exc

        # Own transaction scope
        factory = self._session_factory
        if factory is None:
            from core.database.session import get_engine, get_session_factory

            factory = get_session_factory(get_engine())

        try:
            async with factory() as own_session, own_session.begin():
                await self._repository.insert(decision=decision, session=own_session, snapshot=snapshot)
            return decision
        except Exception as exc:
            logger.error(
                "Failed to persist routing decision for task #%d with owned session: %s",
                snapshot.task_id,
                type(exc).__name__,
            )
            raise RoutingPersistenceError("routing_persistence_failed") from exc

    async def decide(
        self,
        snapshot: TicketSnapshot,
        session: AsyncSession | None = None,
    ) -> RoutingDecision:
        """Alias for analyze_and_store."""
        return await self.analyze_and_store(snapshot=snapshot, session=session)

