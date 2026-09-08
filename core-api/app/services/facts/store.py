"""Persistence adapter for append-only ticket fact observations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.domain import FactObservation

from app.database.db import TicketFactObservation


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TicketFactStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def append(
        self,
        ticket_run_id: uuid.UUID,
        observations: Iterable[FactObservation],
    ) -> list[TicketFactObservation]:
        rows: list[TicketFactObservation] = []
        for observation in observations:
            values = {
                "ticket_run_id": ticket_run_id,
                "fact_key": observation.key,
                "value_json": observation.value,
                "state": observation.state.value,
                "source_kind": observation.source.value,
                "source_ref": observation.source_ref,
                "evidence_span": observation.evidence_span,
                "sensitivity": observation.sensitivity.value,
                "metadata_json": observation.metadata,
                "expires_at": _parse_time(observation.expires_at),
                "schema_version": observation.schema_version,
            }
            observed_at = _parse_time(observation.observed_at)
            if observed_at is not None:
                values["observed_at"] = observed_at
            row = TicketFactObservation(
                **values,
            )
            self.db.add(row)
            rows.append(row)
        await self.db.flush()
        return rows

    async def load(self, ticket_run_id: uuid.UUID) -> list[FactObservation]:
        rows = (
            await self.db.scalars(
                select(TicketFactObservation)
                .where(TicketFactObservation.ticket_run_id == ticket_run_id)
                .order_by(TicketFactObservation.observed_at, TicketFactObservation.id)
            )
        ).all()
        return [
            FactObservation(
                schema_version=row.schema_version,
                key=row.fact_key,
                value=row.value_json,
                state=row.state,
                source=row.source_kind,
                source_ref=row.source_ref,
                evidence_span=row.evidence_span,
                sensitivity=row.sensitivity,
                observed_at=row.observed_at.isoformat(),
                expires_at=row.expires_at.isoformat() if row.expires_at else None,
                metadata=row.metadata_json or {},
            )
            for row in rows
        ]
