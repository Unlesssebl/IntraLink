"""Autopilot Policy Service with 0ms Redis caching, PostgreSQL persistence, and Self-Healing Circuit Breaker."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import redis.asyncio as aioredis
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.autopilot.dto import AutopilotPolicyDTO, AutopilotPolicyUpdateDTO
from core.database.models import AutopilotPolicy
from core.database.session import get_engine, get_session_factory
from core.database.system_state import _get_active_session_factory
from core.redis_client import get_redis_client

logger = logging.getLogger("core.autopilot.policy_service")

# Circuit Breaker thresholds
CIRCUIT_BREAKER_FAILURES_THRESHOLD = 3
CIRCUIT_BREAKER_WINDOW_SEC = 600  # 10 minutes sliding window
REDIS_POLICY_PREFIX = "autopilot:policy:"
REDIS_POLICY_TTL_SEC = 86400  # 24 hours cache TTL

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "autopilot_defaults.yaml"


class AutopilotPolicyService:
    """Manages scenario execution policies, confidence thresholds, and circuit breaker tripwire."""

    def __init__(
        self,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        redis_client: Optional[aioredis.Redis] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        self.session_factory = session_factory
        self.redis_client = redis_client
        self.config_path = config_path or DEFAULT_CONFIG_PATH

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
            logger.debug("Redis client unavailable for autopilot policy service: %s", exc)
            return None

    def load_baseline_yaml(self) -> Dict[str, dict]:
        """Load baseline policies from config/autopilot_defaults.yaml."""
        if not self.config_path.exists():
            logger.warning("Baseline config not found at %s. Using hardcoded fallback.", self.config_path)
            return {
                "install_printer": {"mode": "ASSISTED", "min_confidence": 0.85, "description": "Установка принтеров"},
                "ad_password_reset": {"mode": "ASSISTED", "min_confidence": 0.90, "description": "Сброс паролей AD"},
                "rag_consultation": {"mode": "FULL_AUTO", "min_confidence": 0.85, "description": "RAG консультации"},
            }
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data.get("policies", {})
        except Exception as exc:
            logger.error("Failed to parse %s: %s", self.config_path, exc)
            return {}

    async def get_policy(
        self, scenario_key: str, session: Optional[AsyncSession] = None
    ) -> AutopilotPolicyDTO:
        """Retrieve policy with 0ms Redis check, PostgreSQL fallback, and baseline YAML auto-seeding."""
        redis = self._get_redis()
        cache_key = f"{REDIS_POLICY_PREFIX}{scenario_key}"

        # 1. 0ms Redis cache hit
        if redis is not None:
            try:
                cached = await redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    return AutopilotPolicyDTO.model_validate(data)
            except Exception as exc:
                logger.debug("Redis get error for %s: %s", cache_key, exc)

        # 2. PostgreSQL query
        if session is not None:
            return await self._get_or_create_db_policy(scenario_key, session, redis)

        factory = self._get_session_factory()
        async with factory() as db_session:
            return await self._get_or_create_db_policy(scenario_key, db_session, redis)

    async def _get_or_create_db_policy(
        self,
        scenario_key: str,
        session: AsyncSession,
        redis: Optional[aioredis.Redis],
    ) -> AutopilotPolicyDTO:
        cache_key = f"{REDIS_POLICY_PREFIX}{scenario_key}"
        stmt = select(AutopilotPolicy).where(AutopilotPolicy.scenario_key == scenario_key)
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()

        if record is None:
            # Baseline YAML seed
            baseline = self.load_baseline_yaml().get(scenario_key, {})
            record = AutopilotPolicy(
                scenario_key=scenario_key,
                mode=baseline.get("mode", "ASSISTED"),
                min_confidence=float(baseline.get("min_confidence", 0.85)),
                consecutive_failures=0,
                is_circuit_broken=False,
                description=baseline.get("description"),
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            logger.info("Seeded default AutopilotPolicy for '%s' into PostgreSQL", scenario_key)

        dto = AutopilotPolicyDTO.model_validate(record)

        # Warm Redis cache
        if redis is not None:
            try:
                await redis.set(cache_key, dto.model_dump_json(), ex=REDIS_POLICY_TTL_SEC)
            except Exception as exc:
                logger.debug("Failed to warm Redis for %s: %s", cache_key, exc)

        return dto

    async def list_policies(self, session: Optional[AsyncSession] = None) -> List[AutopilotPolicyDTO]:
        """List all active scenario policies, seeding any unseeded baseline definitions."""
        baseline = self.load_baseline_yaml()

        async def _query(db_sess: AsyncSession) -> List[AutopilotPolicyDTO]:
            stmt = select(AutopilotPolicy)
            res = await db_sess.execute(stmt)
            existing = {r.scenario_key: r for r in res.scalars().all()}

            needs_commit = False
            for key, meta in baseline.items():
                if key not in existing:
                    new_rec = AutopilotPolicy(
                        scenario_key=key,
                        mode=meta.get("mode", "ASSISTED"),
                        min_confidence=float(meta.get("min_confidence", 0.85)),
                        consecutive_failures=0,
                        is_circuit_broken=False,
                        description=meta.get("description"),
                    )
                    db_sess.add(new_rec)
                    existing[key] = new_rec
                    needs_commit = True

            if needs_commit:
                await db_sess.commit()
                for key in baseline:
                    if key in existing:
                        await db_sess.refresh(existing[key])

            dtos = [AutopilotPolicyDTO.model_validate(r) for r in existing.values()]

            # Warm Redis for all
            redis = self._get_redis()
            if redis is not None:
                for dto in dtos:
                    try:
                        await redis.set(
                            f"{REDIS_POLICY_PREFIX}{dto.scenario_key}",
                            dto.model_dump_json(),
                            ex=REDIS_POLICY_TTL_SEC,
                        )
                    except Exception:
                        pass

            return dtos

        if session is not None:
            return await _query(session)

        factory = self._get_session_factory()
        async with factory() as db_session:
            return await _query(db_session)

    async def update_policy(
        self,
        scenario_key: str,
        update_dto: AutopilotPolicyUpdateDTO,
        session: Optional[AsyncSession] = None,
    ) -> AutopilotPolicyDTO:
        """Update scenario policy and reset tripped circuit breaker on human intervention."""
        redis = self._get_redis()
        cache_key = f"{REDIS_POLICY_PREFIX}{scenario_key}"

        async def _update(db_sess: AsyncSession) -> AutopilotPolicyDTO:
            stmt = select(AutopilotPolicy).where(AutopilotPolicy.scenario_key == scenario_key)
            res = await db_sess.execute(stmt)
            record = res.scalar_one_or_none()

            if record is None:
                baseline = self.load_baseline_yaml().get(scenario_key, {})
                record = AutopilotPolicy(
                    scenario_key=scenario_key,
                    mode=update_dto.mode,
                    min_confidence=update_dto.min_confidence or float(baseline.get("min_confidence", 0.85)),
                    consecutive_failures=0,
                    is_circuit_broken=False,
                    description=baseline.get("description"),
                )
                db_sess.add(record)
            else:
                record.mode = update_dto.mode
                if update_dto.min_confidence is not None:
                    record.min_confidence = update_dto.min_confidence
                # Reset circuit breaker on deliberate operator update
                record.is_circuit_broken = False
                record.consecutive_failures = 0

            await db_sess.commit()
            await db_sess.refresh(record)
            dto = AutopilotPolicyDTO.model_validate(record)

            if redis is not None:
                try:
                    await redis.set(cache_key, dto.model_dump_json(), ex=REDIS_POLICY_TTL_SEC)
                except Exception as exc:
                    logger.warning("Failed to update Redis cache for %s: %s", cache_key, exc)

            logger.info("Updated AutopilotPolicy for '%s': mode=%s, min_confidence=%.2f", scenario_key, dto.mode, dto.min_confidence)
            return dto

        if session is not None:
            return await _update(session)

        factory = self._get_session_factory()
        async with factory() as db_session:
            return await _update(db_session)

    async def record_failure(
        self,
        scenario_key: str,
        error: Optional[str] = None,
        session: Optional[AsyncSession] = None,
    ) -> AutopilotPolicyDTO:
        """Record an execution failure and trip circuit breaker to ASSISTED after 3 consecutive failures within 10 min."""
        now = datetime.now(timezone.utc)
        redis = self._get_redis()
        cache_key = f"{REDIS_POLICY_PREFIX}{scenario_key}"

        async def _fail(db_sess: AsyncSession) -> AutopilotPolicyDTO:
            stmt = select(AutopilotPolicy).where(AutopilotPolicy.scenario_key == scenario_key)
            res = await db_sess.execute(stmt)
            record = res.scalar_one_or_none()

            if record is None:
                # Seed first
                await self._get_or_create_db_policy(scenario_key, db_sess, redis)
                res = await db_sess.execute(stmt)
                record = res.scalar_one()

            # Sliding window check
            if record.last_failure_at is not None:
                last_fail = record.last_failure_at
                if last_fail.tzinfo is None:
                    last_fail = last_fail.replace(tzinfo=timezone.utc)
                elapsed = (now - last_fail).total_seconds()
                if elapsed > CIRCUIT_BREAKER_WINDOW_SEC:
                    record.consecutive_failures = 1
                else:
                    record.consecutive_failures += 1
            else:
                record.consecutive_failures = 1

            record.last_failure_at = now

            # Check if threshold reached
            if record.consecutive_failures >= CIRCUIT_BREAKER_FAILURES_THRESHOLD:
                record.is_circuit_broken = True
                if record.mode == "FULL_AUTO":
                    record.mode = "ASSISTED"
                    logger.critical(
                        "🚨 CIRCUIT BREAKER TRIPPED for scenario '%s'! Degraded from FULL_AUTO to ASSISTED after %d errors in %ds. Last error: %s",
                        scenario_key,
                        record.consecutive_failures,
                        CIRCUIT_BREAKER_WINDOW_SEC,
                        error,
                    )

            await db_sess.commit()
            await db_sess.refresh(record)
            dto = AutopilotPolicyDTO.model_validate(record)

            if redis is not None:
                try:
                    await redis.set(cache_key, dto.model_dump_json(), ex=REDIS_POLICY_TTL_SEC)
                except Exception:
                    pass

            return dto

        if session is not None:
            return await _fail(session)

        factory = self._get_session_factory()
        async with factory() as db_session:
            return await _fail(db_session)

    async def record_success(
        self,
        scenario_key: str,
        session: Optional[AsyncSession] = None,
    ) -> AutopilotPolicyDTO:
        """Reset consecutive failures upon successful autonomous execution."""
        redis = self._get_redis()
        cache_key = f"{REDIS_POLICY_PREFIX}{scenario_key}"

        async def _success(db_sess: AsyncSession) -> AutopilotPolicyDTO:
            stmt = select(AutopilotPolicy).where(AutopilotPolicy.scenario_key == scenario_key)
            res = await db_sess.execute(stmt)
            record = res.scalar_one_or_none()

            if record is None:
                return await self._get_or_create_db_policy(scenario_key, db_sess, redis)

            if record.consecutive_failures > 0 or record.is_circuit_broken:
                record.consecutive_failures = 0
                record.is_circuit_broken = False
                await db_sess.commit()
                await db_sess.refresh(record)

            dto = AutopilotPolicyDTO.model_validate(record)

            if redis is not None:
                try:
                    await redis.set(cache_key, dto.model_dump_json(), ex=REDIS_POLICY_TTL_SEC)
                except Exception:
                    pass

            return dto

        if session is not None:
            return await _success(session)

        factory = self._get_session_factory()
        async with factory() as db_session:
            return await _success(db_session)


_global_policy_service: Optional[AutopilotPolicyService] = None


def get_policy_service() -> AutopilotPolicyService:
    """Singleton getter for AutopilotPolicyService."""
    global _global_policy_service
    if _global_policy_service is None:
        _global_policy_service = AutopilotPolicyService()
    return _global_policy_service
