"""Self-Healing Circuit Breaker for Autopilot Scenarios.

Maintains sliding-window failure tracking in Redis (cb:errors:{scenario_key}) over 10 minutes.
Upon 3 consecutive failures:
  1. Trips scenario policy from FULL_AUTO to ASSISTED.
  2. Persists tripped status to PostgreSQL (autopilot_policies and system_state tables).
  3. Emits system event alert to Redis pub/sub and cb:alert:{scenario_key}.
  4. Recovers on successful execution (record_success).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.autopilot.dto import AutopilotPolicyDTO
from core.database.models import AutopilotPolicy, SystemState
from core.database.session import get_engine, get_session_factory
from core.database.system_state import _get_active_session_factory
from core.redis_client import get_redis_client

logger = logging.getLogger("core.autopilot.circuit_breaker")

CIRCUIT_BREAKER_FAILURES_THRESHOLD: int = 3
CIRCUIT_BREAKER_WINDOW_SEC: int = 600  # 10 minutes
REDIS_CB_ERRORS_PREFIX: str = "cb:errors:"
REDIS_CB_ALERT_PREFIX: str = "cb:alert:"
REDIS_POLICY_PREFIX: str = "autopilot:policy:"
REDIS_POLICY_TTL_SEC: int = 86400


class CircuitBreakerStatusDTO(BaseModel):
    """Pydantic v2 DTO representing the state of scenario Circuit Breaker."""

    model_config = ConfigDict(from_attributes=True)

    scenario_key: str
    is_circuit_broken: bool = False
    consecutive_failures: int = 0
    mode: str = "ASSISTED"
    last_failure_at: Optional[datetime] = None
    last_error: Optional[str] = None
    alert_emitted: bool = False


class AutopilotCircuitBreaker:
    """Manages sliding window error tracking and self-healing tripping mechanism."""

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        redis_client: Optional[aioredis.Redis] = None,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURES_THRESHOLD,
        window_seconds: int = CIRCUIT_BREAKER_WINDOW_SEC,
    ) -> None:
        self.session_factory = session_factory
        self.redis_client = redis_client
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        # In-memory sliding window fallback for environments without Redis
        self._in_memory_errors: Dict[str, List[Dict[str, Any]]] = {}

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is not None:
            return self.session_factory
        try:
            return _get_active_session_factory()
        except Exception:
            engine = get_engine()
            return get_session_factory(engine)

    def _get_redis(self) -> Optional[aioredis.Redis]:
        if self.redis_client is not None:
            return self.redis_client
        try:
            return get_redis_client()
        except Exception as exc:
            logger.debug("Redis client unavailable for circuit breaker: %s", exc)
            return None

    async def record_failure(
        self,
        scenario_key: str,
        error: Optional[str] = None,
        task_id: Optional[int] = None,
        session: Optional[AsyncSession] = None,
    ) -> CircuitBreakerStatusDTO:
        """Record scenario execution failure in 10-minute sliding window.

        Trips policy to ASSISTED if 3 consecutive failures occur within the window.
        """
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        cutoff_ts = now_ts - self.window_seconds
        redis = self._get_redis()
        clean_error = error or "Unknown execution error"

        failures_in_window = 0

        # 1. Update sliding window in Redis or in-memory
        if redis is not None:
            cb_key = f"{REDIS_CB_ERRORS_PREFIX}{scenario_key}"
            entry = json.dumps({
                "ts": now_ts,
                "task_id": task_id,
                "error": clean_error[:300],
            })
            try:
                # Add to Sorted Set
                if hasattr(redis, "zadd"):
                    await redis.zadd(cb_key, {entry: now_ts})
                    await redis.zremrangebyscore(cb_key, 0, cutoff_ts)
                    await redis.expire(cb_key, self.window_seconds)
                    failures_in_window = await redis.zcard(cb_key)
                else:
                    # MockRedis / simple client fallback
                    failures_in_window = 1
            except Exception as exc:
                logger.warning("Redis sorted set error for %s: %s", cb_key, exc)
                failures_in_window = 1

        if failures_in_window <= 0:
            # In-memory tracking
            entries = self._in_memory_errors.setdefault(scenario_key, [])
            entries = [e for e in entries if e["ts"] >= cutoff_ts]
            entries.append({"ts": now_ts, "task_id": task_id, "error": clean_error})
            self._in_memory_errors[scenario_key] = entries
            failures_in_window = len(entries)

        # 2. Persist failure & check threshold in PostgreSQL
        async def _update_db(db_sess: AsyncSession) -> CircuitBreakerStatusDTO:
            stmt = select(AutopilotPolicy).where(AutopilotPolicy.scenario_key == scenario_key)
            res = await db_sess.execute(stmt)
            record = res.scalar_one_or_none()

            if record is None:
                record = AutopilotPolicy(
                    scenario_key=scenario_key,
                    mode="ASSISTED",
                    consecutive_failures=0,
                    is_circuit_broken=False,
                )
                db_sess.add(record)

            # Check sliding window on record
            if record.last_failure_at is not None:
                last_fail = record.last_failure_at
                if last_fail.tzinfo is None:
                    last_fail = last_fail.replace(tzinfo=timezone.utc)
                if (now - last_fail).total_seconds() > self.window_seconds:
                    record.consecutive_failures = 1
                else:
                    record.consecutive_failures = max(record.consecutive_failures + 1, failures_in_window)
            else:
                record.consecutive_failures = max(1, failures_in_window)

            record.last_failure_at = now
            tripped_now = False

            # Check tripwire
            if record.consecutive_failures >= self.failure_threshold:
                record.is_circuit_broken = True
                if record.mode == "FULL_AUTO":
                    record.mode = "ASSISTED"
                    tripped_now = True

            # Record system state event in PostgreSQL on trip
            if record.is_circuit_broken:
                stmt_state = select(SystemState).where(SystemState.key == f"cb_alert:{scenario_key}")
                alert_rec = (await db_sess.execute(stmt_state)).scalar_one_or_none()
                alert_data = {
                    "scenario_key": scenario_key,
                    "event": "circuit_breaker_tripped",
                    "consecutive_failures": record.consecutive_failures,
                    "last_error": clean_error,
                    "task_id": task_id,
                    "tripped_at": now.isoformat(),
                }
                if alert_rec is not None:
                    alert_rec.last_poll_at = now
                    alert_rec.last_task_id = task_id
                    alert_rec.state_data = alert_data
                else:
                    db_sess.add(SystemState(
                        key=f"cb_alert:{scenario_key}",
                        last_poll_at=now,
                        last_task_id=task_id,
                        state_data=alert_data,
                    ))

            await db_sess.commit()
            await db_sess.refresh(record)

            # 3. Synchronize with Redis
            if redis is not None:
                try:
                    policy_dto = AutopilotPolicyDTO.model_validate(record)
                    await redis.set(
                        f"{REDIS_POLICY_PREFIX}{scenario_key}",
                        policy_dto.model_dump_json(),
                        ex=REDIS_POLICY_TTL_SEC,
                    )
                    if record.is_circuit_broken:
                        alert_json = json.dumps({
                            "event": "circuit_breaker_tripped",
                            "scenario_key": scenario_key,
                            "failures": record.consecutive_failures,
                            "last_error": clean_error,
                            "task_id": task_id,
                            "timestamp": now.isoformat(),
                        })
                        await redis.set(f"{REDIS_CB_ALERT_PREFIX}{scenario_key}", alert_json, ex=86400)
                        if hasattr(redis, "publish"):
                            await redis.publish("autopilot:events", alert_json)
                except Exception as exc:
                    logger.debug("Redis alert sync error for %s: %s", scenario_key, exc)

            if tripped_now:
                logger.critical(
                    "🚨 CIRCUIT BREAKER TRIPPED for scenario '%s'! Auto-degraded from FULL_AUTO to ASSISTED after %d errors. Last error: %s",
                    scenario_key,
                    record.consecutive_failures,
                    clean_error,
                )

            return CircuitBreakerStatusDTO(
                scenario_key=scenario_key,
                is_circuit_broken=record.is_circuit_broken,
                consecutive_failures=record.consecutive_failures,
                mode=record.mode,
                last_failure_at=record.last_failure_at,
                last_error=clean_error,
                alert_emitted=record.is_circuit_broken,
            )

        if session is not None:
            return await _update_db(session)

        factory = self._get_session_factory()
        async with factory() as db_session:
            return await _update_db(db_session)

    async def record_success(
        self,
        scenario_key: str,
        session: Optional[AsyncSession] = None,
    ) -> CircuitBreakerStatusDTO:
        """Reset failures and circuit breaker state upon successful autonomous execution."""
        redis = self._get_redis()

        # Clear Redis sliding window and alert keys
        if redis is not None:
            try:
                await redis.delete(
                    f"{REDIS_CB_ERRORS_PREFIX}{scenario_key}",
                    f"{REDIS_CB_ALERT_PREFIX}{scenario_key}",
                )
            except Exception as exc:
                logger.debug("Redis clear error for %s: %s", scenario_key, exc)

        self._in_memory_errors.pop(scenario_key, None)

        async def _reset_db(db_sess: AsyncSession) -> CircuitBreakerStatusDTO:
            stmt = select(AutopilotPolicy).where(AutopilotPolicy.scenario_key == scenario_key)
            res = await db_sess.execute(stmt)
            record = res.scalar_one_or_none()

            if record is not None:
                if record.consecutive_failures > 0 or record.is_circuit_broken:
                    record.consecutive_failures = 0
                    record.is_circuit_broken = False
                    await db_sess.commit()
                    await db_sess.refresh(record)

                if redis is not None:
                    try:
                        policy_dto = AutopilotPolicyDTO.model_validate(record)
                        await redis.set(
                            f"{REDIS_POLICY_PREFIX}{scenario_key}",
                            policy_dto.model_dump_json(),
                            ex=REDIS_POLICY_TTL_SEC,
                        )
                    except Exception:
                        pass

                return CircuitBreakerStatusDTO(
                    scenario_key=scenario_key,
                    is_circuit_broken=False,
                    consecutive_failures=0,
                    mode=record.mode,
                    last_failure_at=record.last_failure_at,
                    last_error=None,
                    alert_emitted=False,
                )

            return CircuitBreakerStatusDTO(
                scenario_key=scenario_key,
                is_circuit_broken=False,
                consecutive_failures=0,
                mode="ASSISTED",
                last_failure_at=None,
                last_error=None,
                alert_emitted=False,
            )

        if session is not None:
            return await _reset_db(session)

        factory = self._get_session_factory()
        async with factory() as db_session:
            return await _reset_db(db_session)

    async def get_status(
        self,
        scenario_key: str,
        session: Optional[AsyncSession] = None,
    ) -> CircuitBreakerStatusDTO:
        """Fetch current Circuit Breaker status for scenario."""
        redis = self._get_redis()

        # Check Redis alert first
        if redis is not None:
            try:
                alert_raw = await redis.get(f"{REDIS_CB_ALERT_PREFIX}{scenario_key}")
                if alert_raw:
                    alert_data = json.loads(alert_raw)
                    return CircuitBreakerStatusDTO(
                        scenario_key=scenario_key,
                        is_circuit_broken=True,
                        consecutive_failures=alert_data.get("failures", 3),
                        mode="ASSISTED",
                        last_error=alert_data.get("last_error"),
                        alert_emitted=True,
                    )
            except Exception:
                pass

        async def _query_db(db_sess: AsyncSession) -> CircuitBreakerStatusDTO:
            stmt = select(AutopilotPolicy).where(AutopilotPolicy.scenario_key == scenario_key)
            res = await db_sess.execute(stmt)
            record = res.scalar_one_or_none()

            if record is not None:
                return CircuitBreakerStatusDTO(
                    scenario_key=scenario_key,
                    is_circuit_broken=record.is_circuit_broken,
                    consecutive_failures=record.consecutive_failures,
                    mode=record.mode,
                    last_failure_at=record.last_failure_at,
                    last_error=None,
                    alert_emitted=record.is_circuit_broken,
                )

            return CircuitBreakerStatusDTO(
                scenario_key=scenario_key,
                is_circuit_broken=False,
                consecutive_failures=0,
                mode="ASSISTED",
            )

        if session is not None:
            return await _query_db(session)

        factory = self._get_session_factory()
        async with factory() as db_session:
            return await _query_db(db_session)

    async def is_tripped(
        self,
        scenario_key: str,
        session: Optional[AsyncSession] = None,
    ) -> bool:
        """Check if Circuit Breaker is currently tripped for scenario."""
        status = await self.get_status(scenario_key, session=session)
        return status.is_circuit_broken


_global_circuit_breaker: Optional[AutopilotCircuitBreaker] = None


def get_circuit_breaker() -> AutopilotCircuitBreaker:
    """Singleton getter for AutopilotCircuitBreaker."""
    global _global_circuit_breaker
    if _global_circuit_breaker is None:
        _global_circuit_breaker = AutopilotCircuitBreaker()
    return _global_circuit_breaker
