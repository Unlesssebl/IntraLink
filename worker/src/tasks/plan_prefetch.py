"""Background task pre-calculating AgentPlanDTO during ingestion for instant 0 ms UI read-through."""

import logging
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis

from core.autopilot.dto import AgentPlanDTO
from core.autopilot.intent import detect_tense_tone
from core.autopilot.policy_service import AutopilotPolicyService, get_policy_service
from core.diagnostic.ports import FastSocketProbe
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO
from core.intraservice.parser import PC_EXTRACT_REGEX
from core.redis_client import get_redis_client
from worker.src.broker import QUEUE_DEFAULT, broker
from worker.src.scenarios.base import BaseScenario
from worker.src.scenarios.registry import ScenarioRegistry, get_default_scenario_registry
from worker.src.services.auth import ServiceAuthBootstrap, ServiceAuthCredentials

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

    try:
        auth: ServiceAuthCredentials = await service_auth.bootstrap_auth(
            client=client,
            redis_client=redis_conn,
        )
        task: TaskDTO = await client.get_task(task_id=ticket_id, auth_b64=auth.auth_b64)
    except Exception as exc:
        logger.debug("Plan prefetch failed to fetch ticket #%d: %s", ticket_id, exc)
        return {"status": "failed", "ticket_id": ticket_id, "error": str(exc)}

    # 1. Candidate hosts discovery & mood tagging
    raw_text = f"{task.name} {task.description or ''}"
    is_tense, tense_reason = detect_tense_tone(raw_text)
    has_attachments = bool(task.attachments)

    candidate_matches = PC_EXTRACT_REGEX.findall(raw_text)
    cleaned_candidates = list(dict.fromkeys(c.strip().upper() for c in candidate_matches if len(c.strip()) >= 3))
    if task.entities.pc_name and task.entities.pc_name.upper() not in cleaned_candidates:
        cleaned_candidates.insert(0, task.entities.pc_name.upper())

    # 2. Scenario routing
    scenario: Optional[BaseScenario] = await registry.find_scenario(task)
    scenario_key = scenario.scenario_key if scenario else "unmatched"
    scenario_name = scenario.name if scenario else "Ручной разбор (сценарий не определен)"
    description = scenario.description if scenario else "Заявка передана на ручную классификацию оператору"

    # 3. Policy & circuit breaker
    policy = await policy_service.get_policy(scenario_key)
    is_circuit_broken = policy.is_circuit_broken
    mode = policy.mode

    # 4. Evaluation match and preconditions
    confidence = 0.0
    factor_breakdown: Dict[str, float] = {}
    preconditions_dict: Dict[str, Any] = {"is_valid": False, "missing_facts": [], "environment_barriers": []}
    suggested_comment = ""
    target_status_id = 3

    if scenario:
        match_res = await scenario.evaluate_match(task)
        confidence = match_res.confidence
        factor_breakdown = {"confidence": confidence}
        precond_res = await scenario.validate_preconditions(task)
        preconditions_dict = precond_res.model_dump()

        if scenario_key == "install_printer":
            suggested_comment = f"Здравствуйте! Сетевой принтер настроен на вашем рабочем месте {task.entities.pc_name or ''}."
        elif scenario_key == "grant_wlan":
            suggested_comment = "Здравствуйте! Доступ к сети WLAN-WORKNET предоставлен для вашей учетной записи."
        elif scenario_key == "service_redirect":
            suggested_comment = "Заявка отменена, т. к. создана не в подходящем разделе каталога."
            target_status_id = 30
        elif scenario_key == "offline_host":
            suggested_comment = f"Здравствуйте! Компьютер {task.entities.pc_name or ''} недоступен в корпоративной сети."
            target_status_id = 6

    # 5. Fast network diagnostic probe
    host_diag_dto = None
    target_host = task.entities.pc_name or (cleaned_candidates[0] if cleaned_candidates else None)
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

    # 6. Dialogue state
    dialogue_state = None
    last_event_id = None
    try:
        lifetimes: List[TaskLifetimeEventDTO] = await client.get_task_lifetime(
            task_id=ticket_id, auth_b64=auth.auth_b64
        )
        rounds = sum(1 for e in lifetimes if e.status_id == 6)
        last_event_id = lifetimes[-1].id if lifetimes else None
        dialogue_state = {
            "rounds": rounds,
            "is_waiting_for_applicant": task.status_id == 6,
            "total_events": len(lifetimes),
        }
    except Exception:
        pass

    plan = AgentPlanDTO(
        task_id=task.id,
        scenario_key=scenario_key,
        scenario_name=scenario_name,
        description=description,
        confidence=confidence,
        matched=scenario is not None and confidence >= policy.min_confidence,
        factor_breakdown=factor_breakdown,
        preconditions=preconditions_dict,
        host_diagnostic=host_diag_dto,
        extracted_entities=task.entities.model_dump(),
        candidate_hosts=cleaned_candidates,
        proposed_action=scenario_key,
        proposed_params=task.entities.model_dump(),
        suggested_comment=suggested_comment,
        target_status_id=target_status_id,
        dialogue_state=dialogue_state,
        command_id=None,
        last_event_id=last_event_id,
        is_circuit_broken=is_circuit_broken,
        mode=mode,
        is_tense=is_tense,
        tense_reason=tense_reason,
        has_attachments=has_attachments,
    )

    # Cache in Redis with 300s TTL (True 0 ms Delivery for UI)
    if redis_conn is not None:
        try:
            cache_key = f"cache:autopilot:plan:{ticket_id}"
            await redis_conn.set(cache_key, plan.model_dump_json(), ex=300)
            logger.info("Prefetched and cached agent plan for ticket #%d in Redis (TTL 300s)", ticket_id)
        except Exception as exc:
            logger.debug("Failed to cache prefetched plan in Redis: %s", exc)

    return {"status": "prefetched", "ticket_id": ticket_id, "scenario": scenario_key}
