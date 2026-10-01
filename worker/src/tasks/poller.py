"""Read-only queue monitoring with dual-slice querying and a durable watermark.

Pulse interval: every 30 seconds.
Slices:
  1. Active IT queue filter (filterid=984).
  2. Incremental updates (ChangedMoreThan=<watermark>).
Backoff:
  30s -> 60s -> 120s on network/5xx server errors. Resets to 30s on success.
Automation:
  - Assignment of the configured service account creates one durable autonomy run.
  - Polling dispatches new, retryable and operator-unblocked runs through Core API.
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

from core.automation.autonomy import AutonomyFailureReporter, AutonomyRunCoordinator
from core.automation.clarification import latest_human_public_event
from core.database.models import (
    AutonomyRunRecord,
    ClarificationRequestRecord,
    CommandRecord,
    ExecutionPermitRecord,
    SystemState,
    WorkflowPlanRecord,
)
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
from core.intraservice.statuses import TaskStatusResolver
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

        delay = self.DEFAULT_POLL_INTERVAL_SEC * (self.BACKOFF_FACTOR ** min(self.consecutive_errors, 2))
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
        # 6. Project assignment edges and dispatch newly-created durable runs.
        await self._observe_assignments(unique_tasks, auth, session=session)
        # 7. Update dual watermark in PostgreSQL & Redis
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
            "Poll iteration complete: %d filter tasks, %d changed tasks, %d unique, %d observed. Next interval: %.1fs",
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
                        baseline_event_key=clarification.baseline_event_key,
                        bot_user_id=auth.bot_user_id,
                        applicant_id=tasks[task_id].applicant_id,
                        question_text=clarification.question_text,
                    )
                    if event is None or event.id is None:
                        continue
                    if clarification.last_error_code == f"no_facts_in_event:{event.id}":
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
            if response.status_code not in (200, 409, 422):
                response.raise_for_status()
        finally:
            if owned:
                await client.aclose()

    async def _observe_assignments(
        self,
        tasks: Dict[int, TaskDTO],
        auth: ServiceAuthCredentials,
        *,
        session: Optional[AsyncSession] = None,
    ) -> None:
        if not self.worker_api_key or not auth.bot_user_id or not tasks:
            return

        coordinator = AutonomyRunCoordinator()
        resolver = TaskStatusResolver()

        async def project_and_dispatch(db: AsyncSession) -> None:
            baseline_state = await db.get(SystemState, "autonomy:assignment_baseline")
            baseline = baseline_state is None or not bool((baseline_state.state_data or {}).get("completed"))
            run_ids = []
            for task in tasks.values():
                try:
                    finality = await resolver.resolve(
                        db,
                        self.client,
                        status_id=task.status_id,
                        auth_b64=auth.auth_b64,
                    )
                    lifetime = await self.client.get_task_lifetime(
                        task_id=task.id,
                        auth_b64=auth.auth_b64,
                    )
                    observation = await coordinator.observe(
                        db,
                        task=task,
                        bot_user_id=auth.bot_user_id,
                        is_final=bool(finality.is_final) if finality is not None else False,
                        lifetime=lifetime,
                        bot_display_name=auth.display_name,
                        baseline=baseline,
                    )
                    if observation.run is not None and finality is None and observation.run.state != "cancelled":
                        observation.run.state = "retry_wait"
                        observation.run.blocker_code = "status_unknown"
                        observation.run.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=30)
                    if (
                        observation.created
                        and observation.run is not None
                        and not observation.cancelled
                        and finality is not None
                    ):
                        run_ids.append(observation.run.id)
                    elif observation.run is not None and observation.run.state == "detected":
                        run_ids.append(observation.run.id)
                except Exception as exc:
                    logger.warning("Assignment projection failed for task %s: %s", task.id, exc)

            if baseline:
                if baseline_state is None:
                    baseline_state = SystemState(key="autonomy:assignment_baseline", state_data={})
                    db.add(baseline_state)
                baseline_state.state_data = {
                    "completed": True,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
                baseline_state.last_poll_at = datetime.now(timezone.utc)
            if run_ids:
                dispatching_runs = list(
                    (
                        await db.scalars(
                            select(AutonomyRunRecord).where(
                                AutonomyRunRecord.id.in_(run_ids)
                            )
                        )
                    ).all()
                )
                for item in dispatching_runs:
                    item.state = "dispatching"
            await db.commit()

            # A worker crash after consuming a permit is never retried.  A stale
            # running command is reconciled to unknown_outcome for safe review.
            stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
            stale_commands = list(
                (
                    await db.scalars(
                        select(CommandRecord)
                        .join(
                            ExecutionPermitRecord,
                            ExecutionPermitRecord.id == CommandRecord.execution_permit_id,
                        )
                        .where(
                            CommandRecord.autonomy_run_id.is_not(None),
                            CommandRecord.status == "running",
                            CommandRecord.updated_at < stale_cutoff,
                            ExecutionPermitRecord.consumed_at.is_not(None),
                        )
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for command in stale_commands:
                command.status = "unknown_outcome"
                command.error_message = "worker_lost_after_permit_consumption"
                run = await db.get(AutonomyRunRecord, command.autonomy_run_id)
                if run is not None:
                    run.state = "unknown_outcome"
                    run.blocker_code = "unknown_outcome"
                    run.completed_at = datetime.now(timezone.utc)
                    await AutonomyFailureReporter().enqueue(
                        db,
                        run=run,
                        phase="execution",
                        blocker_code="unknown_outcome",
                    )
            if stale_commands:
                await db.commit()

            # Pending notes are independent of run advancement and survive API outages.
            try:
                await AutonomyFailureReporter().deliver_pending(
                    db,
                    client=self.client,
                    auth_b64=auth.auth_b64,
                )
                await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.warning("Autonomy comment outbox delivery failed: %s", exc)

            stale_dispatches = list(
                (
                    await db.scalars(
                        select(AutonomyRunRecord).where(
                            AutonomyRunRecord.state == "dispatching",
                            AutonomyRunRecord.updated_at
                            < datetime.now(timezone.utc) - timedelta(minutes=2),
                        )
                    )
                ).all()
            )
            for item in stale_dispatches:
                item.state = "retry_wait"
                item.blocker_code = "core_api_unavailable"
                item.next_attempt_at = datetime.now(timezone.utc)
            if stale_dispatches:
                await db.commit()

            due_runs = list(
                (
                    await db.scalars(
                        select(AutonomyRunRecord).where(
                            (
                                (AutonomyRunRecord.state == "ready")
                                | (
                                    (AutonomyRunRecord.state == "retry_wait")
                                    & (
                                        AutonomyRunRecord.next_attempt_at
                                        <= datetime.now(timezone.utc)
                                    )
                                )
                            )
                        )
                    )
                ).all()
            )
            for item in due_runs:
                item.state = "dispatching"
                item.next_attempt_at = None
                run_ids.append(item.id)
            if due_runs:
                await db.commit()
            sem = asyncio.Semaphore(3)

            async def dispatch(run_id: object) -> tuple[object, bool]:
                async with sem:
                    try:
                        await self._post_run(run_id)
                    except Exception as exc:
                        logger.warning("Autonomy run dispatch failed for %s: %s", run_id, exc)
                        return run_id, False
                    return run_id, True

            results = await asyncio.gather(
                *(dispatch(run_id) for run_id in dict.fromkeys(run_ids))
            )
            for run_id, delivered in results:
                if delivered:
                    continue
                failed_run = await db.get(AutonomyRunRecord, run_id)
                if failed_run is not None and failed_run.state == "dispatching":
                    failed_run.state = "retry_wait"
                    failed_run.blocker_code = "core_api_unavailable"
                    failed_run.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            if any(not delivered for _, delivered in results):
                await db.commit()

        if session is not None:
            await project_and_dispatch(session)
        elif self.session_factory is not None:
            async with self.session_factory() as owned_session:
                await project_and_dispatch(owned_session)

    async def _post_run(self, run_id: object) -> bool:
        client = self.http_client or httpx.AsyncClient(timeout=120.0)
        owned = self.http_client is None
        try:
            response = await client.post(
                f"{self.resume_base_url}/internal/autopilot/runs/{run_id}/advance",
                headers={"X-Worker-Key": self.worker_api_key},
            )
            if response.status_code == 409:
                return True
            response.raise_for_status()
            return True
        finally:
            if owned:
                await client.aclose()

    async def _post_intake(self, task_id: int) -> bool:
        client = self.http_client or httpx.AsyncClient(timeout=60.0)
        owned = self.http_client is None
        try:
            response = await client.post(
                f"{self.resume_base_url}/internal/autopilot/tickets/{task_id}/intake",
                headers={"X-Worker-Key": self.worker_api_key},
            )
            if response.status_code == 409:
                return True
            if response.status_code not in (200, 422):
                response.raise_for_status()
            return response.status_code == 200
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
