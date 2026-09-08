"""Persistence adapter for append-only ticket fact observations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.domain import FactObservation

from app.database.db import TicketFactObservation


def _to_utc_dt(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        dt = value
    else:
        return None

    from datetime import timezone
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_time(value: str | datetime | None) -> datetime | None:
    return _to_utc_dt(value)


class TicketFactStore:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def append(
        self,
        ticket_run_id: uuid.UUID,
        observations: Iterable[FactObservation],
    ) -> list[TicketFactObservation]:
        from datetime import timedelta, timezone
        latest_stored = await self.db.scalar(
            select(TicketFactObservation.observed_at)
            .where(TicketFactObservation.ticket_run_id == ticket_run_id)
            .order_by(TicketFactObservation.observed_at.desc(), TicketFactObservation.id.desc())
            .limit(1)
        )
        current_mono = _to_utc_dt(latest_stored)
        now_utc = datetime.now(timezone.utc)

        rows: list[TicketFactObservation] = []
        for observation in observations:
            obs_time = _to_utc_dt(observation.observed_at) or now_utc
            if current_mono is not None and obs_time <= current_mono:
                obs_time = current_mono + timedelta(microseconds=1000)
            current_mono = obs_time

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
                "observed_at": obs_time,
                "expires_at": _to_utc_dt(observation.expires_at),
                "schema_version": observation.schema_version,
            }
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
                .order_by(TicketFactObservation.observed_at.asc(), TicketFactObservation.id.asc())
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
