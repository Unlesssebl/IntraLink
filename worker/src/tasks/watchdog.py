"""Inactivity Watchdog service for monitoring suspended tickets (Status 6).

Invariants (Edge Case 12):
1. Status 6 tickets without applicant reply for >= 48 hours receive an automated courteous reminder.
2. An anti-spam Redis flag (watchdog:reminder_sent:{task_id}) guarantees no repeated reminders.
3. Status 6 tickets without applicant reply for >= 120 hours (5 business days) are automatically
   cancelled (Status 30) with a standard regulatory notice and internal audit entry.
4. Uses single direct transitions to IntraService API without intermediate status hops.
"""

import logging
from datetime import UTC, datetime
from typing import Any, Optional

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.database.models import CommandRecord
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO
from core.redis_client import get_redis_client
from worker.src.broker import QUEUE_DEFAULT, broker

logger = logging.getLogger("worker.tasks.watchdog")

REMINDER_HOURS = 48.0
CANCEL_HOURS = 120.0
REMINDER_FLAG_TTL_SEC = 864000  # 10 days
STUCK_TICKET_THRESHOLD_SEC = 180.0  # 3 minutes

# Test override hooks
_override_client: IntraServiceClient | None = None
_override_service_auth: ServiceAuthBootstrap | None = None
_override_redis_client: aioredis.Redis | None = None
_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_override_autopilot_task: Any = None


def set_watchdog_client(client: IntraServiceClient | None) -> None:
    global _override_client
    _override_client = client


def set_watchdog_service_auth(auth_bootstrap: ServiceAuthBootstrap | None) -> None:
    global _override_service_auth
    _override_service_auth = auth_bootstrap


def set_watchdog_redis_client(redis_conn: aioredis.Redis | None) -> None:
    global _override_redis_client
    _override_redis_client = redis_conn


def set_watchdog_session_factory(factory: Optional[async_sessionmaker[AsyncSession]]) -> None:
    global _override_session_factory
    _override_session_factory = factory


def set_watchdog_autopilot_task(task_obj: Any) -> None:
    global _override_autopilot_task
    _override_autopilot_task = task_obj


def _get_client() -> IntraServiceClient:
    if _override_client is not None:
        return _override_client
    return IntraServiceClient()


def _get_service_auth() -> ServiceAuthBootstrap:
    if _override_service_auth is not None:
        return _override_service_auth
    return ServiceAuthBootstrap()


def _get_session_factory() -> Optional[async_sessionmaker[AsyncSession]]:
    if _override_session_factory is not None:
        return _override_session_factory
    try:
        from core.database.system_state import _get_active_session_factory
        return _get_active_session_factory()
    except Exception:
        return None


def _get_autopilot_task() -> Any:
    if _override_autopilot_task is not None:
        return _override_autopilot_task
    from worker.src.tasks.autopilot import autopilot_task
    return autopilot_task


def _get_redis() -> aioredis.Redis | None:
    if _override_redis_client is not None:
        return _override_redis_client
    try:
        return get_redis_client()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Redis unavailable for watchdog: %s", exc)
        return None


def parse_ticket_last_activity(task: TaskDTO) -> datetime:
    """Extract UTC timestamp of the most recent activity on the ticket."""
    raw = getattr(task, "changed", None) or getattr(task, "created", None)
    if not raw:
        return datetime.now(UTC)
    try:
        clean_raw = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:  # noqa: BLE001
        return datetime.now(UTC)


class InactivityWatchdog:
    """Core domain logic for processing inactive Status 6 tickets."""

    def __init__(
        self,
        reminder_hours: float = REMINDER_HOURS,
        cancel_hours: float = CANCEL_HOURS,
    ) -> None:
        self.reminder_hours = reminder_hours
        self.cancel_hours = cancel_hours

    async def evaluate_and_process_ticket(
        self,
        task: TaskDTO,
        auth: ServiceAuthCredentials,
        client: IntraServiceClient,
        redis_conn: aioredis.Redis | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Check elapsed suspension time and execute action if thresholds exceeded."""
        current_time = now or datetime.now(UTC)
        last_active = parse_ticket_last_activity(task)
        elapsed_hours = max(0.0, (current_time - last_active).total_seconds() / 3600.0)

        # Invariant: only process tickets currently in Status 6 (Suspended)
        if task.status_id != 6:
            return {
                "task_id": task.id,
                "action": "skipped_not_status_6",
                "status_id": task.status_id,
            }

        # 1. 120 Hours Threshold: Auto-Cancel
        if elapsed_hours >= self.cancel_hours:
            logger.info(
                "Ticket #%d suspended for %.1f hours (>= %.1f). Auto-cancelling (Status 30).",
                task.id,
                elapsed_hours,
                self.cancel_hours,
            )
            # Step 1.1: Direct PUT transition to Status 30 with applicant resolution message
            await client.update_task(
                task_id=task.id,
                status_id=30,  # Отменена
                comment=(
                    "Здравствуйте! Заявка отменена автоматически в связи с отсутствием ответа "
                    "заявителя в течение 5 рабочих дней. Если вопрос по-прежнему актуален, "
                    "пожалуйста, создайте новое обращение."
                ),
                is_private=False,
                auth_b64=auth.auth_b64,
            )
            # Step 1.2: Post hidden internal technical audit note
            await client.update_task(
                task_id=task.id,
                comment=(
                    f"🤖 [Inactivity Watchdog: Авто-закрытие по таймауту]\n"
                    f"Заявитель не предоставил ответ на уточнение в течение {int(elapsed_hours)} ч "
                    f"(порог: {int(self.cancel_hours)} ч / 5 дней). "
                    "Статус переведен в 30 (Отменена)."
                ),
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            # Step 1.3: Clean up reminder flag
            if redis_conn is not None:
                try:
                    await redis_conn.delete(f"watchdog:reminder_sent:{task.id}")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to cleanup reminder flag in Redis: %s", exc)

            return {
                "task_id": task.id,
                "action": "auto_cancelled",
                "elapsed_hours": round(elapsed_hours, 1),
            }

        # 2. 48 Hours Threshold: Automated Courtesy Reminder
        if elapsed_hours >= self.reminder_hours:
            reminder_key = f"watchdog:reminder_sent:{task.id}"
            already_reminded = False
            if redis_conn is not None:
                try:
                    already_reminded = bool(await redis_conn.exists(reminder_key))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Redis reminder check error for ticket #%d: %s", task.id, exc)

            if already_reminded:
                return {
                    "task_id": task.id,
                    "action": "reminder_already_sent",
                    "elapsed_hours": round(elapsed_hours, 1),
                }

            logger.info(
                "Ticket #%d suspended for %.1f hours (>= %.1f). Sending courtesy reminder.",
                task.id,
                elapsed_hours,
                self.reminder_hours,
            )
            # Step 2.1: Post courteous public reminder
            await client.update_task(
                task_id=task.id,
                comment=(
                    "Здравствуйте! Напоминаем о необходимости предоставить запрошенные ранее данные "
                    "для решения вашего обращения. Пожалуйста, ответьте на это сообщение, "
                    "чтобы мы могли продолжить работу по заявке."
                ),
                is_private=False,
                auth_b64=auth.auth_b64,
            )
            # Step 2.2: Post internal technical audit note
            await client.update_task(
                task_id=task.id,
                comment=(
                    f"🤖 [Inactivity Watchdog: Напоминание заявителю]\n"
                    f"С момента перевода в статус 6 прошло {int(elapsed_hours)} ч без ответа. "
                    "Заявителю отправлено напоминание."
                ),
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            # Step 2.3: Set anti-spam flag in Redis
            if redis_conn is not None:
                try:
                    await redis_conn.set(reminder_key, "1", ex=REMINDER_FLAG_TTL_SEC)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to set reminder flag in Redis: %s", exc)

            return {
                "task_id": task.id,
                "action": "reminder_sent",
                "elapsed_hours": round(elapsed_hours, 1),
            }

        # 3. Within normal waiting window
        return {
            "task_id": task.id,
            "action": "waiting",
            "elapsed_hours": round(elapsed_hours, 1),
        }

    async def reconcile_stuck_in_progress_tickets(
        self,
        auth: ServiceAuthCredentials,
        client: IntraServiceClient,
        redis_conn: aioredis.Redis | None = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        now: datetime | None = None,
        stuck_threshold_sec: float = STUCK_TICKET_THRESHOLD_SEC,
    ) -> list[dict[str, Any]]:
        """Identify and revive orphaned tickets stuck in Status 2 (In Progress) without active worker process."""
        current_time = now or datetime.now(UTC)
        results: list[dict[str, Any]] = []

        try:
            tasks_in_progress: list[TaskDTO] = await client.get_tasks(
                filters={"statusid": 2},
                page_size=200,
                auth_b64=auth.auth_b64,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch in-progress tickets for reconciliation: %s", exc)
            return results

        # Filter only tickets assigned to service bot
        bot_tasks = [
            t for t in tasks_in_progress
            if auth.bot_user_id is not None and auth.bot_user_id in t.get_executor_ids()
        ]

        for task in bot_tasks:
            try:
                last_active = parse_ticket_last_activity(task)
                elapsed_sec = max(0.0, (current_time - last_active).total_seconds())

                if elapsed_sec < stuck_threshold_sec:
                    # Still within normal execution grace window (< 3 min)
                    continue

                # Check Redis in-flight concurrency locks
                if redis_conn is not None:
                    lock_canonical = await redis_conn.exists(f"lock:task:{task.id}")
                    lock_legacy = await redis_conn.exists(f"lock:autopilot:{task.id}")
                    if lock_canonical or lock_legacy:
                        # Ticket is actively being processed by a running worker
                        continue

                # Check database for pending or running command records
                if session_factory is not None:
                    try:
                        async with session_factory() as db_session:
                            stmt = select(CommandRecord.id).where(
                                CommandRecord.task_id == task.id,
                                CommandRecord.status.in_(["pending", "running"]),
                            ).limit(1)
                            db_res = await db_session.execute(stmt)
                            if db_res.scalar_one_or_none() is not None:
                                # External command (e.g. ActionDock/WMI) is still executing
                                continue
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("DB check for active commands in ticket #%d failed: %s", task.id, exc)

                # Check anti-storm reconciliation retry counter
                recon_retry_key = f"watchdog:reconcile_retry:{task.id}"
                retries = 0
                if redis_conn is not None:
                    try:
                        val = await redis_conn.get(recon_retry_key)
                        retries = int(val) if val else 0
                    except Exception:
                        retries = 0

                if retries >= 3:
                    logger.warning(
                        "Ticket #%d exceeded max self-healing reconciliation attempts (%d). Escalating to human.",
                        task.id,
                        retries,
                    )
                    await client.update_task(
                        task_id=task.id,
                        comment=(
                            "🤖 [Inactivity Watchdog: Превышен лимит самоисцеления]\n"
                            "Заявка неоднократно зависала в статусе 'В работе' без активного процесса (3 попытки). "
                            "Автопилот остановлен для предотвращения зацикливания. Требуется ручной разбор дежурным инженером."
                        ),
                        is_private=True,
                        auth_b64=auth.auth_b64,
                    )
                    results.append({"task_id": task.id, "action": "escalated_max_retries", "retries": retries})
                    continue

                # Perform Self-Healing revival
                logger.info(
                    "Ticket #%d stuck in Status 2 for %.1fs (>= %.1fs) without active worker lock. Reviving autopilot.",
                    task.id,
                    elapsed_sec,
                    stuck_threshold_sec,
                )

                if redis_conn is not None:
                    try:
                        await redis_conn.incr(recon_retry_key)
                        await redis_conn.expire(recon_retry_key, 600)  # 10 min TTL
                        await redis_conn.delete(f"autopilot:abort:{task.id}")
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Redis error updating retry counter for #%d: %s", task.id, exc)

                # Post internal technical audit entry
                await client.update_task(
                    task_id=task.id,
                    comment=(
                        "🤖 [Inactivity Watchdog: Self-Healing Reconciliation]\n"
                        f"Обнаружено зависание заявки в статусе 'В работе' без активного процесса ({int(elapsed_sec)} сек). "
                        "Автопилот перезапущен автоматически."
                    ),
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )

                # Retrigger autopilot task in Taskiq
                task_fn = _get_autopilot_task()
                if task_fn is not None:
                    await task_fn.kiq(task_id=task.id)

                results.append({
                    "task_id": task.id,
                    "action": "reconciled_autopilot_retriggered",
                    "elapsed_sec": round(elapsed_sec, 1),
                })
            except Exception:
                logger.exception("Error reconciling in-progress ticket #%d", task.id)
                results.append({"task_id": task.id, "action": "error"})

        return results


@broker.task(task_name="inactivity_watchdog_task", queue_name=QUEUE_DEFAULT)
async def inactivity_watchdog_task() -> dict[str, Any]:
    """Periodic Taskiq task inspecting suspended tickets (Status 6) and reconciling stuck in-progress tickets (Status 2)."""
    logger.info("Executing Inactivity Watchdog inspection & reconciliation...")
    client = _get_client()
    service_auth = _get_service_auth()
    redis_conn = _get_redis()
    session_factory = _get_session_factory()
    watchdog = InactivityWatchdog()

    try:
        auth: ServiceAuthCredentials = await service_auth.bootstrap_auth(
            client=client,
            redis_client=redis_conn,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Watchdog authentication failed: %s", exc)
        return {"status": "failed", "error": f"auth_error: {exc}"}

    # 1. Fetch and process tickets in Status 6 (Suspended)
    try:
        suspended_tasks: list[TaskDTO] = await client.get_tasks(
            filters={"statusid": 6},
            page_size=200,
            auth_b64=auth.auth_b64,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch suspended tickets: %s", exc)
        suspended_tasks = []

    results = []
    processed_count = 0
    reminders_count = 0
    cancelled_count = 0

    now = datetime.now(UTC)
    for task in suspended_tasks:
        try:
            res = await watchdog.evaluate_and_process_ticket(
                task=task,
                auth=auth,
                client=client,
                redis_conn=redis_conn,
                now=now,
            )
            results.append(res)
            processed_count += 1
            if res.get("action") == "reminder_sent":
                reminders_count += 1
            elif res.get("action") == "auto_cancelled":
                cancelled_count += 1
        except Exception:
            logger.exception("Error processing ticket #%d in watchdog", task.id)
            results.append({"task_id": task.id, "action": "error"})

    # 2. Self-Healing Reconciliation for stuck in-progress tickets (Status 2)
    reconciled_results = await watchdog.reconcile_stuck_in_progress_tickets(
        auth=auth,
        client=client,
        redis_conn=redis_conn,
        session_factory=session_factory,
        now=now,
    )
    reconciled_count = sum(1 for r in reconciled_results if r.get("action") == "reconciled_autopilot_retriggered")
    results.extend(reconciled_results)

    logger.info(
        "Watchdog finished: inspected %d suspended (%d reminders, %d auto-cancelled), reconciled %d stuck tickets.",
        processed_count,
        reminders_count,
        cancelled_count,
        reconciled_count,
    )
    return {
        "status": "completed",
        "inspected": processed_count,
        "reminders_sent": reminders_count,
        "auto_cancelled": cancelled_count,
        "reconciled_stuck_tickets": reconciled_count,
        "details": results,
    }
