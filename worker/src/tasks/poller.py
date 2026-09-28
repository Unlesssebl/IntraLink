"""Read-only queue monitoring with dual-slice querying and a durable watermark.

Pulse interval: every 30 seconds.
Slices:
  1. Active IT queue filter (filterid=984).
  2. Incremental updates (ChangedMoreThan=<watermark>).
Backoff:
  30s -> 60s -> 120s on network/5xx server errors. Resets to 30s on success.
Automation:
  - Polling never analyzes or mutates tickets.
  - Operators explicitly invoke the ADR 0006 analyze/approve flow.
Watermark:
  - Dual persistent state: Redis system-state cache + PostgreSQL (system_state table).
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.automation.clarification import latest_human_public_event
from core.database.models import ClarificationRequestRecord, WorkflowPlanRecord
from core.database.system_state import (
    WatermarkService,
    _get_active_session_factory,
)
from core.intraservice.auth import (
    ServiceAuthBootstrap,
    ServiceAuthCredentials,
)
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO
from core.intraservice.polling import (
    PollerStepResult,
)
from core.redis_client import get_redis_client
from worker.src.broker import QUEUE_DEFAULT, broker

PollStepResult = PollerStepResult

logger = logging.getLogger("worker.tasks.poller")


class IngestionPoller:
    """IntraService ingestion poller implementing dual-slice fetching, watermark persistence and event lock idempotency."""

    DEFAULT_POLL_INTERVAL_SEC: float = 30.0
    MAX_BACKOFF_INTERVAL_SEC: float = 120.0
    BACKOFF_FACTOR: float = 2.0
    FILTER_ACTIVE_IT: int = 984
    WATERMARK_KEY: str = "ingestion_poller"
    def __init__(
        self,
        service_auth: Optional[ServiceAuthBootstrap] = None,
        watermark_service: Optional[WatermarkService] = None,
        client: Optional[IntraServiceClient] = None,
        redis_client: Optional[aioredis.Redis] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        resume_base_url: Optional[str] = None,
        worker_api_key: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.service_auth = service_auth or ServiceAuthBootstrap()
        self.watermark_service = watermark_service or WatermarkService(
            session_factory=session_factory,
            redis_client=redis_client,
        )
        self.client = client or IntraServiceClient()
        self.redis_client = redis_client
        self.session_factory = session_factory
        self.resume_base_url = (resume_base_url or os.getenv("CORE_API_URL") or "http://api:8000/api/v2").rstrip("/")
        self.worker_api_key = worker_api_key if worker_api_key is not None else os.getenv("WORKER_API_KEY", "")
        self.http_client = http_client

        self.consecutive_errors: int = 0
        self.current_interval_sec: float = self.DEFAULT_POLL_INTERVAL_SEC

    def get_current_interval(self) -> float:
        """Calculate polling interval based on consecutive error count."""
        if self.consecutive_errors <= 0:
            return self.DEFAULT_POLL_INTERVAL_SEC

        delay = self.DEFAULT_POLL_INTERVAL_SEC * (
            self.BACKOFF_FACTOR ** min(self.consecutive_errors, 2)
        )
        return min(delay, self.MAX_BACKOFF_INTERVAL_SEC)

    def record_success(self) -> None:
        """Reset consecutive error count and backoff delay upon successful request."""
        self.consecutive_errors = 0
        self.current_interval_sec = self.DEFAULT_POLL_INTERVAL_SEC

    def record_failure(self, exc: Exception) -> float:
        """Increment consecutive error count and compute backed off interval."""
        self.consecutive_errors += 1
        self.current_interval_sec = self.get_current_interval()
        return self.current_interval_sec

    async def poll_step(self, session: Optional[AsyncSession] = None) -> PollerStepResult:
        """Execute a single polling iteration.

        1. Ensures bot authentication.
        2. Retrieves latest watermark cursor (Redis/Postgres).
        3. Executes dual-slice query (Filter 984 + ChangedMoreThan).
        4. Deduplicates observed tickets without dispatching automation.
        5. Updates the durable watermark in PostgreSQL and its Redis cache.
        """
        now_utc = datetime.now(timezone.utc)

        # 1. Bot credentials verification
        try:
            auth: ServiceAuthCredentials = await self.service_auth.bootstrap_auth(
                client=self.client,
                redis_client=self.redis_client,
            )
        except Exception as exc:
            next_interval = self.record_failure(exc)
            logger.error("Service bot authentication failed during poll step: %s", exc)
            return PollerStepResult(
                polled_at=now_utc,
                filter_tasks_count=0,
                changed_tasks_count=0,
                unique_tasks_count=0,
                observed_tasks_count=0,
                next_interval_sec=next_interval,
                consecutive_errors=self.consecutive_errors,
                error=f"Auth error: {exc}",
            )

        # 2. Retrieve Watermark
        try:
            watermark = await self.watermark_service.get_watermark(
                key=self.WATERMARK_KEY,
                session=session,
                redis_client=self.redis_client,
            )
        except Exception as exc:
            logger.warning("Failed to retrieve watermark: %s. Using default fallback.", exc)
            watermark = None

        # Determine cutoff timestamp for ChangedMoreThan
        if watermark and watermark.last_poll_at is not None:
            # Overlap by 2 minutes to protect against server clock skew and in-flight transactions
            cutoff = watermark.last_poll_at - timedelta(minutes=2)
        else:
            # Cold-start fallback: look back 30 minutes
            cutoff = now_utc - timedelta(minutes=30)

        changed_more_than_str = cutoff.strftime("%Y-%m-%d %H:%M")

        # 3. Dual-slice fetch with backoff protection
        try:
            tasks_filter: List[TaskDTO] = await self.client.get_tasks_by_filter(
                filter_id=self.FILTER_ACTIVE_IT,
                auth_b64=auth.auth_b64,
            )
            tasks_changed: List[TaskDTO] = await self.client.get_tasks(
                filters={"ChangedMoreThan": changed_more_than_str},
                auth_b64=auth.auth_b64,
            )
            self.record_success()
        except Exception as exc:
            next_interval = self.record_failure(exc)
            logger.warning(
                "IntraService polling failed (%s). Consecutive errors: %d. Backing off to %.1fs",
                exc,
                self.consecutive_errors,
                next_interval,
            )
            return PollerStepResult(
                polled_at=now_utc,
                filter_tasks_count=0,
                changed_tasks_count=0,
                unique_tasks_count=0,
                observed_tasks_count=0,
                next_interval_sec=next_interval,
                consecutive_errors=self.consecutive_errors,
                error=f"IntraService fetch error: {exc}",
            )

        # 4. Deduplicate tickets by task.id
        unique_tasks: Dict[int, TaskDTO] = {}
        for t in tasks_filter:
            unique_tasks[t.id] = t
        for t in tasks_changed:
            unique_tasks[t.id] = t

        # 5. Resume only an already awaiting assisted workflow when a new public human answer exists.
        await self._resume_waiting_clarifications(unique_tasks, auth, session=session)
        # 6. Update dual watermark in PostgreSQL & Redis
        max_task_id = (watermark.last_task_id if watermark else None) or 0
        if unique_tasks:
            current_max = max(unique_tasks.keys())
            max_task_id = max(max_task_id, current_max)

        try:
            await self.watermark_service.set_watermark(
                key=self.WATERMARK_KEY,
                last_poll_at=now_utc,
                last_task_id=max_task_id if max_task_id > 0 else None,
                session=session,
                redis_client=self.redis_client,
            )
        except Exception as exc:
            logger.error("Failed to persist updated watermark: %s", exc)

        logger.info(
            "Poll iteration complete: %d filter tasks, %d changed tasks, %d unique, "
            "%d observed. Next interval: %.1fs",
            len(tasks_filter),
            len(tasks_changed),
            len(unique_tasks),
            len(unique_tasks),
            self.current_interval_sec,
        )

        return PollerStepResult(
            polled_at=now_utc,
            filter_tasks_count=len(tasks_filter),
            changed_tasks_count=len(tasks_changed),
            unique_tasks_count=len(unique_tasks),
            observed_tasks_count=len(unique_tasks),
            next_interval_sec=self.current_interval_sec,
            consecutive_errors=0,
            error=None,
        )

    async def _resume_waiting_clarifications(
        self,
        tasks: Dict[int, TaskDTO],
        auth: ServiceAuthCredentials,
        *,
        session: Optional[AsyncSession],
    ) -> None:
        if not self.worker_api_key or not tasks:
            return

        async def process(db: AsyncSession) -> None:
            for task_id in tasks:
                latest = await db.scalar(
                    select(WorkflowPlanRecord)
                    .where(WorkflowPlanRecord.task_id == task_id)
                    .order_by(WorkflowPlanRecord.created_at.desc(), WorkflowPlanRecord.id.desc())
                    .limit(1)
                )
                if latest is None or latest.state != "awaiting_facts":
                    continue
                clarification = await db.scalar(
                    select(ClarificationRequestRecord)
                    .where(
                        ClarificationRequestRecord.task_id == task_id,
                        ClarificationRequestRecord.state == "published",
                    )
                    .order_by(ClarificationRequestRecord.published_at.desc())
                    .limit(1)
                )
                if clarification is None:
                    continue
                try:
                    events = await self.client.get_task_lifetime(task_id=task_id, auth_b64=auth.auth_b64)
                    event = latest_human_public_event(
                        events,
                        baseline_event_id=clarification.baseline_event_id,
                        bot_user_id=auth.bot_user_id,
                    )
                    if event is None or event.id is None:
                        continue
                    await self._post_resume(task_id, event.id)
                except Exception as exc:
                    logger.warning("Clarification resume failed for task %s: %s", task_id, exc)

        if session is not None:
            await process(session)
        elif self.session_factory is not None:
            async with self.session_factory() as owned_session:
                await process(owned_session)

    async def _post_resume(self, task_id: int, event_id: int) -> None:
        client = self.http_client or httpx.AsyncClient(timeout=30.0)
        owned = self.http_client is None
        try:
            response = await client.post(
                f"{self.resume_base_url}/internal/autopilot/tickets/{task_id}/resume",
                json={"event_id": event_id},
                headers={"X-Worker-Key": self.worker_api_key},
            )
            if response.status_code not in (200, 409):
                response.raise_for_status()
        finally:
            if owned:
                await client.aclose()


_active_poller: Optional[IngestionPoller] = None


def get_active_poller() -> IngestionPoller:
    """Return or initialize global IngestionPoller instance."""
    global _active_poller
    if _active_poller is None:
        redis_conn = get_redis_client()
        session_factory = _get_active_session_factory()
        _active_poller = IngestionPoller(
            redis_client=redis_conn,
            session_factory=session_factory,
        )
    return _active_poller


def set_active_poller(poller: Optional[IngestionPoller]) -> None:
    """Override active poller instance for testing."""
    global _active_poller
    _active_poller = poller


@broker.task(task_name="poll_queue_task", queue_name=QUEUE_DEFAULT)
async def poll_queue_task() -> Dict[str, Any]:
    """Taskiq task executing a single iteration of queue polling and ingestion."""
    poller = get_active_poller()
    res = await poller.poll_step()
    return res.model_dump()


async def run_poller_loop(
    poller: Optional[IngestionPoller] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Continuous 30-second pulse polling loop with exponential backoff on failure."""
    p = poller or get_active_poller()
    logger.info("Ingestion poller loop started (pulse: %.1fs)", p.DEFAULT_POLL_INTERVAL_SEC)

    while not (stop_event and stop_event.is_set()):
        try:
            res = await p.poll_step()
            delay = res.next_interval_sec
        except Exception as exc:
            logger.exception("Unexpected error during poller step: %s", exc)
            delay = p.record_failure(exc)

        if stop_event and stop_event.is_set():
            break

        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                break
            else:
                await asyncio.sleep(delay)
        except asyncio.TimeoutError:
            pass

    logger.info("Ingestion poller loop terminated cleanly.")
