"""Background task pre-calculating Evidence-Based Routing plan during ingestion.

Uses TicketAnalysisService as the canonical analysis pipeline, ensuring identical
decision, preflight and persistence behavior across API and worker.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

from core.autopilot.policy_service import AutopilotPolicyService, get_policy_service
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from core.intraservice.client import IntraServiceClient
from core.redis_client import get_redis_client
from core.routing.analysis_service import TicketAnalysisService
from worker.src.broker import QUEUE_DEFAULT, broker

logger = logging.getLogger("worker.tasks.plan_prefetch")

# Test override hooks
_override_client: Optional[IntraServiceClient] = None
_override_service_auth: Optional[ServiceAuthBootstrap] = None
_override_redis_client: Optional[aioredis.Redis] = None
_override_policy_service: Optional[AutopilotPolicyService] = None
_override_analysis_service: Optional[TicketAnalysisService] = None


def set_prefetch_client(client: Optional[IntraServiceClient]) -> None:
    global _override_client
    _override_client = client


def set_prefetch_service_auth(auth_bootstrap: Optional[ServiceAuthBootstrap]) -> None:
    global _override_service_auth
    _override_service_auth = auth_bootstrap


def set_prefetch_redis_client(redis_conn: Optional[aioredis.Redis]) -> None:
    global _override_redis_client
    _override_redis_client = redis_conn


def set_prefetch_policy_service(service: Optional[AutopilotPolicyService]) -> None:
    global _override_policy_service
    _override_policy_service = service


def set_prefetch_analysis_service(service: Optional[TicketAnalysisService]) -> None:
    global _override_analysis_service
    _override_analysis_service = service


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


def _get_policy_service() -> AutopilotPolicyService:
    if _override_policy_service is not None:
        return _override_policy_service
    return get_policy_service()


def _get_analysis_service() -> TicketAnalysisService:
    if _override_analysis_service is not None:
        return _override_analysis_service
    return TicketAnalysisService(
        client=_get_client(),
        policy_service=_get_policy_service(),
    )


@broker.task(task_name="prefetch_agent_plan_task", queue_name=QUEUE_DEFAULT)
async def prefetch_agent_plan_task(ticket_id: int) -> Dict[str, Any]:
    """Background task executing Evidence Routing Cascade analysis and caching prepared plan in Redis and PostgreSQL."""
    client = _get_client()
    service_auth = _get_service_auth()
    redis_conn = _get_redis()
    analysis_service = _get_analysis_service()

    # 1. Authenticate service bot
    auth_b64: Optional[str] = None
    try:
        auth: ServiceAuthCredentials = await service_auth.bootstrap_auth(
            client=client,
            redis_client=redis_conn,
        )
        auth_b64 = auth.auth_b64
    except Exception as exc:
        logger.debug("Plan prefetch auth bootstrap failed for ticket #%d: %s", ticket_id, exc)

    # 2. Run Evidence Routing Cascade analysis
    try:
        plan = await analysis_service.analyze_ticket(
            ticket_id=ticket_id,
            force=False,
            auth_b64=auth_b64,
            redis_client=redis_conn,
        )
        logger.info(
            "Prefetched and persisted evidence plan for ticket #%d (state: %s, scenario: %s, plan_hash: %s)",
            ticket_id,
            plan.routing_state,
            plan.scenario_key,
            plan.plan_hash,
        )
        return {
            "status": "prefetched",
            "ticket_id": ticket_id,
            "scenario": plan.scenario_key,
            "routing_state": plan.routing_state,
            "plan_hash": plan.plan_hash,
        }
    except Exception as exc:
        logger.warning("Plan prefetch failed for ticket #%d: %s", ticket_id, exc)
        return {"status": "failed", "ticket_id": ticket_id, "error": str(exc)}
