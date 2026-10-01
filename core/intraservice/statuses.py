"""Canonical IntraService status finality resolver.

IntraService owns status semantics.  Numeric IDs and IsFixed are deliberately
not used to decide whether an automation run may execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.models import SystemState
from core.intraservice.client import IntraServiceClient


@dataclass(frozen=True)
class StatusFinality:
    status_id: int
    name: str
    is_final: bool
    is_fixed: bool
    fetched_at: datetime


class TaskStatusResolver:
    STATE_KEY = "intraservice:task_status_catalog"
    REFRESH_AFTER = timedelta(minutes=5)
    MAX_AGE = timedelta(minutes=15)

    async def resolve(
        self,
        session: AsyncSession,
        client: IntraServiceClient,
        *,
        status_id: int,
        auth_b64: str,
        force_refresh: bool = False,
    ) -> StatusFinality | None:
        now = datetime.now(UTC)
        state = await session.scalar(select(SystemState).where(SystemState.key == self.STATE_KEY))
        data = dict(state.state_data or {}) if state is not None else {}
        fetched_at = self._parse_timestamp(data.get("fetched_at"))
        statuses = data.get("statuses") if isinstance(data.get("statuses"), dict) else {}
        cached = statuses.get(str(status_id)) if isinstance(statuses, dict) else None

        must_refresh = (
            force_refresh
            or fetched_at is None
            or now - fetched_at >= self.REFRESH_AFTER
            or not isinstance(cached, dict)
        )
        if must_refresh:
            try:
                live = await client.get_statuses(auth_b64=auth_b64)
            except Exception:
                live = []
            if live:
                fetched_at = now
                statuses = {
                    str(item.id): {
                        "name": item.name,
                        "is_final": item.is_final,
                        "is_fixed": item.is_fixed,
                    }
                    for item in live
                }
                if state is None:
                    state = SystemState(key=self.STATE_KEY, state_data={})
                    session.add(state)
                state.state_data = {
                    "fetched_at": fetched_at.isoformat(),
                    "statuses": statuses,
                }
                state.last_poll_at = fetched_at
                await session.flush()
                cached = statuses.get(str(status_id))

        if fetched_at is None or now - fetched_at > self.MAX_AGE or not isinstance(cached, dict):
            return None
        return StatusFinality(
            status_id=status_id,
            name=str(cached.get("name") or f"Статус {status_id}"),
            is_final=bool(cached.get("is_final")),
            is_fixed=bool(cached.get("is_fixed")),
            fetched_at=fetched_at,
        )

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
