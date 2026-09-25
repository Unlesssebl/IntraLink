"""Background task pre-calculating AgentPlanDTO during ingestion for instant 0 ms UI read-through.

Uses PlanSynthesizer (core.scenarios.engine) as the canonical synthesis engine,
eliminating ~110 lines of duplicated plan assembly that formerly lived in this file.
"""

import logging
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

from core.autopilot.policy_service import AutopilotPolicyService, get_policy_service
from core.diagnostic.ports import FastSocketProbe
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from core.redis_client import get_redis_client
from core.scenarios.engine import PlanSynthesizer
from core.scenarios.registry import ScenarioRegistry, get_default_scenario_registry
from worker.src.broker import QUEUE_DEFAULT, broker

logger = logging.getLogger("worker.tasks.plan_prefetch")

# Test override hooks
_override_client: Optional[IntraServiceClient] = None
_override_service_auth: Optional[ServiceAuthBootstrap] = None
_override_redis_client: Optional[aioredis.Redis] = None
_override_registry: Optional[ScenarioRegistry] = None
_override_policy_service: Optional[AutopilotPolicyService] = None


def set_prefetch_client(client: Optional[IntraServiceClient]) -> None:
    global _override_client
    _override_client = client


def set_prefetch_service_auth(auth_bootstrap: Optional[ServiceAuthBootstrap]) -> None:
    global _override_service_auth
    _override_service_auth = auth_bootstrap


def set_prefetch_redis_client(redis_conn: Optional[aioredis.Redis]) -> None:
    global _override_redis_client
    _override_redis_client = redis_conn


def set_prefetch_registry(registry: Optional[ScenarioRegistry]) -> None:
    global _override_registry
    _override_registry = registry


def set_prefetch_policy_service(service: Optional[AutopilotPolicyService]) -> None:
    global _override_policy_service
    _override_policy_service = service


def _get_client() -> IntraServiceClient:
    if _override_client is not None:
        return _override_client
    return IntraServiceClient()


def _get_service_auth() -> ServiceAuthBootstrap:
    if _override_service_auth is not None:
        return _override_service_auth
    return ServiceAuthBootstrap()


def _get_redis() -> Optional[aioredis.Redis]:
    if _override_redis_client is not None:
        return _override_redis_client
    try:
        return get_redis_client()
    except Exception as exc:
        logger.debug("Redis unavailable for plan_prefetch: %s", exc)
        return None


def _get_registry() -> ScenarioRegistry:
    if _override_registry is not None:
        return _override_registry
    return get_default_scenario_registry()


def _get_policy_service() -> AutopilotPolicyService:
    if _override_policy_service is not None:
        return _override_policy_service
    return get_policy_service()


@broker.task(task_name="prefetch_agent_plan_task", queue_name=QUEUE_DEFAULT)
async def prefetch_agent_plan_task(ticket_id: int) -> Dict[str, Any]:
    """Background task synthesizing AgentPlanDTO and caching in Redis (cache:autopilot:plan:{ticket_id})."""
    client = _get_client()
    service_auth = _get_service_auth()
    redis_conn = _get_redis()
    registry = _get_registry()
    policy_service = _get_policy_service()

    # 1. Authenticate and fetch ticket
    try:
        auth: ServiceAuthCredentials = await service_auth.bootstrap_auth(
            client=client,
            redis_client=redis_conn,
        )
        task: TaskDTO = await client.get_task(task_id=ticket_id, auth_b64=auth.auth_b64)
    except Exception as exc:
        logger.debug("Plan prefetch failed to fetch ticket #%d: %s", ticket_id, exc)
        return {"status": "failed", "ticket_id": ticket_id, "error": str(exc)}

    # 2. Fast network diagnostic probe (worker-context: bare port check, no caching)
    host_diag_dto = None
    target_host = task.entities.pc_name
    if target_host:
        try:
            smb_ok = await FastSocketProbe.probe(target_host, 445, timeout=1.0)
            winrm_ok = await FastSocketProbe.probe(target_host, 5985, timeout=1.0)
            host_diag_dto = {
                "hostname": target_host,
                "is_reachable": smb_ok or winrm_ok,
                "ports": {"445": smb_ok, "5985": winrm_ok},
            }
        except Exception as exc:
            logger.debug("Diagnostics probe failed for %s: %s", target_host, exc)

    # 3. Dialogue state (lightweight: counts only, no full lifetime fetch)
    dialogue_state = None
    last_event_id = None
    try:
        lifetimes = await client.get_task_lifetime(task_id=ticket_id, auth_b64=auth.auth_b64)
        rounds = sum(1 for e in lifetimes if e.status_id == 6)
        last_event_id = lifetimes[-1].id if lifetimes else None
        dialogue_state = {
            "rounds": rounds,
            "is_waiting_for_applicant": task.status_id == 6,
            "total_events": len(lifetimes),
        }
    except Exception:
        pass

    # 4. Synthesize plan via PlanSynthesizer and cache
    synthesizer = PlanSynthesizer(registry=registry, policy_service=policy_service)
    plan = await synthesizer.synthesize_and_cache(
        task,
        redis_conn,
        host_diag=host_diag_dto,
        dialogue_state=dialogue_state,
        last_event_id=last_event_id,
    )

    logger.info(
        "Prefetched and cached agent plan for ticket #%d in Redis (TTL 300s, scenario: %s)",
        ticket_id,
        plan.scenario_key,
    )
    return {"status": "prefetched", "ticket_id": ticket_id, "scenario": plan.scenario_key}
