"""Queue Monitoring & Ingestion Poller with Dual-Slice Querying, Watermark and Event Lock.

Pulse interval: every 30 seconds.
Slices:
  1. Active IT queue filter (filterid=984).
  2. Incremental updates (ChangedMoreThan=<watermark>).
Backoff:
  30s -> 60s -> 120s on network/5xx server errors. Resets to 30s on success.
Watermark:
  Dual persistent state: Redis (autopilot:watermark:ts, autopilot:watermark:task_id) + PostgreSQL (system_state table).
Concurrency & Idempotency:
  - Single-Flight: checks active in-flight worker locks (lock:task:{id}).
  - Event Lock: suppresses redundant enqueuing for unmodified ticket events within 300s window.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

import redis.asyncio as aioredis
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from core.redis_client import get_redis_client

logger = logging.getLogger("core.intraservice.poller")


def get_executor_ids_list(task: TaskDTO) -> List[int]:
    """Parse comma-separated ExecutorIds string into a list of integer IDs."""
    if not task.executor_ids:
        return []
    result = []
    for part in str(task.executor_ids).split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return result


class PollerStepResult(BaseModel):
    """Result summary of a single polling iteration."""

    polled_at: datetime
    filter_tasks_count: int
    changed_tasks_count: int
    unique_tasks_count: int
    triaged_tasks_count: int
    autopilot_tasks_count: int
    next_interval_sec: float
    consecutive_errors: int = 0
    error: Optional[str] = None


PollStepResult = PollerStepResult


class IngestionPoller:
    """IntraService ingestion poller implementing dual-slice fetching, watermark persistence and event lock idempotency."""

    DEFAULT_POLL_INTERVAL_SEC: float = 30.0
    MAX_BACKOFF_INTERVAL_SEC: float = 120.0
    BACKOFF_FACTOR: float = 2.0
    FILTER_ACTIVE_IT: int = 984
    WATERMARK_KEY: str = "ingestion_poller"
    EVENT_LOCK_TTL_SEC: int = 300  # 5 minutes de-duplication window

    def __init__(
        self,
        service_auth: Optional[ServiceAuthBootstrap] = None,
        watermark_service: Optional[WatermarkService] = None,
        client: Optional[IntraServiceClient] = None,
        redis_client: Optional[aioredis.Redis] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        on_enqueue_triage: Optional[Callable[[int], Coroutine[Any, Any, None]]] = None,
        on_enqueue_autopilot: Optional[Callable[[int], Coroutine[Any, Any, None]]] = None,
    ) -> None:
        self.service_auth = service_auth or ServiceAuthBootstrap()
        self.watermark_service = watermark_service or WatermarkService(
            session_factory=session_factory,
            redis_client=redis_client,
        )
        self.client = client or IntraServiceClient()
        self.redis_client = redis_client
        self.session_factory = session_factory
        self._on_enqueue_triage = on_enqueue_triage
        self._on_enqueue_autopilot = on_enqueue_autopilot

        self.consecutive_errors: int = 0
        self.current_interval_sec: float = self.DEFAULT_POLL_INTERVAL_SEC
        self._in_memory_event_cache: Set[str] = set()

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

    async def _is_event_locked(self, queue_type: str, task: TaskDTO) -> bool:
        """Check Single-Flight concurrency lock and Event Lock for ticket."""
        event_signature = f"{task.id}:{task.changed or ''}:{task.status_id}"
        event_hash = hashlib.md5(event_signature.encode()).hexdigest()[:12]

        if self.redis_client is not None:
            try:
                # 1. Single-Flight Concurrency check: is ticket actively being processed?
                inflight_lock = await self.redis_client.exists(f"lock:task:{task.id}")
                if inflight_lock:
                    logger.debug(
                        "Ticket #%d is actively being executed by a worker (Single-Flight lock). Skipping enqueue.",
                        task.id,
                    )
                    return True

                # 2. Event Lock: has this exact ticket state already been enqueued?
                event_key = f"poller:event:{queue_type}:{task.id}:{event_hash}"
                acquired = await self.redis_client.set(
                    event_key,
                    "enqueued",
                    nx=True,
                    ex=self.EVENT_LOCK_TTL_SEC,
                )
                if not acquired:
                    logger.debug(
                        "Ticket #%d event state is already locked (%s). Suppressing duplicate enqueue.",
                        task.id,
                        event_key,
                    )
                    return True
                return False
            except Exception as exc:
                logger.debug("Redis error in _is_event_locked for task #%d: %s", task.id, exc)

        # In-memory fallback
        in_mem_key = f"{queue_type}:{event_signature}"
        if in_mem_key in self._in_memory_event_cache:
            return True
        self._in_memory_event_cache.add(in_mem_key)
        if len(self._in_memory_event_cache) > 2000:
            self._in_memory_event_cache.clear()
        return False

    async def poll_step(self, session: Optional[AsyncSession] = None) -> PollerStepResult:
        """Execute a single polling iteration.

        1. Ensures bot authentication.
        2. Retrieves latest watermark cursor (Redis/Postgres).
        3. Executes dual-slice query (Filter 984 + ChangedMoreThan).
        4. Deduplicates and routes tickets to triage or autopilot queues with Event Lock protection.
        5. Updates dual watermark in Postgres and Redis (autopilot:watermark:ts, autopilot:watermark:task_id).
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
                triaged_tasks_count=0,
                autopilot_tasks_count=0,
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
            # Overlap by 2 minutes to protect against clock skew
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
                triaged_tasks_count=0,
                autopilot_tasks_count=0,
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

        # 5. Route tickets with Event Lock and Single-Flight protection
        triaged_count = 0
        autopilot_count = 0

        for task in unique_tasks.values():
            executors = get_executor_ids_list(task)
            if not executors:
                # Unassigned ticket -> enqueue in triage
                if await self._is_event_locked("triage", task):
                    continue
                logger.debug("Enqueuing unassigned ticket #%d in triage_task", task.id)
                if self._on_enqueue_triage is not None:
                    await self._on_enqueue_triage(task.id)
                triaged_count += 1
            elif auth.bot_user_id in executors:
                # Bot assigned ticket -> enqueue in autopilot
                if await self._is_event_locked("autopilot", task):
                    continue
                logger.debug("Enqueuing bot ticket #%d in autopilot_task", task.id)
                if self._on_enqueue_autopilot is not None:
                    await self._on_enqueue_autopilot(task.id)
                autopilot_count += 1
            else:
                # Assigned to human engineer -> do not interfere
                pass

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
            "%d triaged, %d autopilot. Next interval: %.1fs",
            len(tasks_filter),
            len(tasks_changed),
            len(unique_tasks),
            triaged_count,
            autopilot_count,
            self.current_interval_sec,
        )

        return PollerStepResult(
            polled_at=now_utc,
            filter_tasks_count=len(tasks_filter),
            changed_tasks_count=len(tasks_changed),
            unique_tasks_count=len(unique_tasks),
            triaged_tasks_count=triaged_count,
            autopilot_tasks_count=autopilot_count,
            next_interval_sec=self.current_interval_sec,
            consecutive_errors=0,
            error=None,
        )
