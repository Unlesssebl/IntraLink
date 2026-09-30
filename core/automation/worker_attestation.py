"""Worker attestation and heartbeat contract for ADR 0006.

Enforces strict worker health, policy engine version compatibility,
and deployment ceiling attestation over Redis.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from pydantic import BaseModel, ConfigDict, Field

from core.automation.execution_policy import POLICY_ENGINE_VERSION

logger = logging.getLogger("core.automation.worker_attestation")

WORKER_HEARTBEAT_PREFIX = "automation:worker:"
WORKER_HEARTBEAT_TTL_SECONDS = 30
WORKER_HEARTBEAT_INTERVAL_SECONDS = 10


class WorkerHeartbeat(BaseModel):
    """Payload published by active workers every 10 seconds with 30s TTL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    capabilities: list[str] = Field(default_factory=list)
    executor_types: list[str] = Field(default_factory=list)
    policy_engine_version: str = POLICY_ENGINE_VERSION
    force_disabled: bool = True
    config_fingerprint: str
    heartbeat_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class WorkerAttestationResult:
    """Evaluated attestation state for a specific capability."""

    worker_available: bool
    worker_compatible: bool
    worker_force_disabled: bool
    active_nodes_count: int
    matching_nodes: list[str]
    reasons: list[str]

    @property
    def healthy(self) -> bool:
        return self.worker_available and self.worker_compatible and not self.worker_force_disabled


def compute_config_fingerprint(
    *,
    engine_version: str,
    capabilities: list[str],
    executor_types: list[str],
    force_disabled: bool,
) -> str:
    raw = {
        "capabilities": sorted(capabilities),
        "engine_version": engine_version,
        "executor_types": sorted(executor_types),
        "force_disabled": force_disabled,
    }
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class WorkerAttestationService:
    """Manages worker heartbeat publishing and compatibility verification."""

    def __init__(self, engine_version: str = POLICY_ENGINE_VERSION) -> None:
        self.engine_version = engine_version

    @staticmethod
    def heartbeat_key(node_id: str) -> str:
        return f"{WORKER_HEARTBEAT_PREFIX}{node_id}"

    async def publish_heartbeat(
        self,
        redis_client: aioredis.Redis,
        *,
        node_id: str,
        capabilities: list[str],
        executor_types: list[str],
        force_disabled: bool,
    ) -> WorkerHeartbeat:
        fingerprint = compute_config_fingerprint(
            engine_version=self.engine_version,
            capabilities=capabilities,
            executor_types=executor_types,
            force_disabled=force_disabled,
        )
        heartbeat = WorkerHeartbeat(
            node_id=node_id,
            capabilities=sorted(capabilities),
            executor_types=sorted(executor_types),
            policy_engine_version=self.engine_version,
            force_disabled=force_disabled,
            config_fingerprint=fingerprint,
            heartbeat_at=datetime.now(UTC),
        )
        key = self.heartbeat_key(node_id)
        payload = heartbeat.model_dump_json()
        await redis_client.set(key, payload, ex=WORKER_HEARTBEAT_TTL_SECONDS)
        return heartbeat

    async def get_active_workers(
        self, redis_client: aioredis.Redis | None
    ) -> list[WorkerHeartbeat]:
        if redis_client is None:
            return []
        try:
            pattern = f"{WORKER_HEARTBEAT_PREFIX}*"
            keys = await redis_client.keys(pattern)
            if not keys:
                return []
            records: list[WorkerHeartbeat] = []
            for raw_key in keys:
                key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
                raw_data = await redis_client.get(key)
                if not raw_data:
                    continue
                try:
                    payload = json.loads(raw_data)
                    records.append(WorkerHeartbeat.model_validate(payload))
                except Exception as exc:
                    logger.warning("Failed to decode worker heartbeat from %s: %s", key, exc)
            return records
        except Exception as exc:
            logger.warning("Redis failure while reading worker heartbeats: %s", exc)
            return []

    async def verify_worker_for_capability(
        self,
        redis_client: aioredis.Redis | None,
        capability_key: str,
    ) -> WorkerAttestationResult:
        return await self.check_capability_worker(redis_client, capability_key=capability_key)

    async def check_capability_worker(
        self,
        redis_client: aioredis.Redis | None,
        *,
        capability_key: str,
    ) -> WorkerAttestationResult:
        if redis_client is None:
            return WorkerAttestationResult(
                worker_available=False,
                worker_compatible=False,
                worker_force_disabled=False,
                active_nodes_count=0,
                matching_nodes=[],
                reasons=["redis_unavailable", "worker_unavailable"],
            )

        active_workers = await self.get_active_workers(redis_client)
        if not active_workers:
            return WorkerAttestationResult(
                worker_available=False,
                worker_compatible=False,
                worker_force_disabled=False,
                active_nodes_count=0,
                matching_nodes=[],
                reasons=["worker_unavailable"],
            )

        capable_workers = [
            w for w in active_workers if capability_key in w.capabilities
        ]
        if not capable_workers:
            return WorkerAttestationResult(
                worker_available=False,
                worker_compatible=False,
                worker_force_disabled=False,
                active_nodes_count=len(active_workers),
                matching_nodes=[],
                reasons=["worker_unavailable"],
            )

        compatible_workers = [
            w for w in capable_workers if w.policy_engine_version == self.engine_version
        ]
        if not compatible_workers:
            return WorkerAttestationResult(
                worker_available=True,
                worker_compatible=False,
                worker_force_disabled=False,
                active_nodes_count=len(active_workers),
                matching_nodes=[w.node_id for w in capable_workers],
                reasons=["worker_incompatible"],
            )

        ready_workers = [w for w in compatible_workers if not w.force_disabled]
        if not ready_workers:
            return WorkerAttestationResult(
                worker_available=True,
                worker_compatible=True,
                worker_force_disabled=True,
                active_nodes_count=len(active_workers),
                matching_nodes=[w.node_id for w in compatible_workers],
                reasons=["worker_force_disabled"],
            )

        return WorkerAttestationResult(
            worker_available=True,
            worker_compatible=True,
            worker_force_disabled=False,
            active_nodes_count=len(active_workers),
            matching_nodes=[w.node_id for w in ready_workers],
            reasons=[],
        )
