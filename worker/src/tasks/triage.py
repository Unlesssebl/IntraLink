"""Background triage task for unassigned tickets with Relevance Gateway and Anti-Loop Guard.

Enforces:
1. Anti-Loop Guard: suppresses auto-replies, bounces and bot loops.
2. Optimistic Lock (Read-before-Write): validates ticket has not been closed or claimed by an engineer.
3. Relevance Gateway (Filter #1): cancels and redirects 100% irrelevant tickets (Status 30)
   with public guidelines and hidden private technical audit note.
4. Immutable PostgreSQL audit recording in `triage_audit` table.
"""

import logging
from typing import Any, Dict, Optional

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.database.models import TriageAudit
from core.database.session import get_engine, get_session_factory
from core.database.system_state import _get_active_session_factory
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO
from core.redis_client import get_redis_client
from core.triage.gateway import RelevanceDecision, RelevanceGateway
from worker.src.broker import QUEUE_DEFAULT, broker
from core.autopilot.dialogue import AntiLoopGuard
from core.autopilot.policy_service import AutopilotPolicyService
from core.intraservice.auth import (
    ServiceAuthBootstrap,
    ServiceAuthCredentials,
)
from core.scenarios.registry import ScenarioRegistry, get_default_scenario_registry

logger = logging.getLogger("worker.tasks.triage")

# Test override hooks
_override_client: Optional[IntraServiceClient] = None
_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_override_service_auth: Optional[ServiceAuthBootstrap] = None
_override_redis_client: Optional[aioredis.Redis] = None
_override_registry: Optional[ScenarioRegistry] = None
_override_policy_service: Optional[AutopilotPolicyService] = None


def set_triage_client(client: Optional[IntraServiceClient]) -> None:
    """Set custom IntraServiceClient instance (for testing)."""
    global _override_client
    _override_client = client


def set_triage_session_factory(factory: Optional[async_sessionmaker[AsyncSession]]) -> None:
    """Set custom session factory (for testing)."""
    global _override_session_factory
    _override_session_factory = factory


def set_triage_service_auth(auth_bootstrap: Optional[ServiceAuthBootstrap]) -> None:
    """Set custom ServiceAuthBootstrap instance (for testing)."""
    global _override_service_auth
    _override_service_auth = auth_bootstrap


def set_triage_redis_client(redis_conn: Optional[aioredis.Redis]) -> None:
    """Set custom Redis connection (for testing)."""
    global _override_redis_client
    _override_redis_client = redis_conn


def set_triage_registry(registry: Optional[ScenarioRegistry]) -> None:
    """Set custom ScenarioRegistry instance (for testing)."""
    global _override_registry
    _override_registry = registry


def set_triage_policy_service(policy_service: Optional[AutopilotPolicyService]) -> None:
    """Set custom AutopilotPolicyService instance (for testing)."""
    global _override_policy_service
    _override_policy_service = policy_service


def _get_client() -> IntraServiceClient:
    if _override_client is not None:
        return _override_client
    return IntraServiceClient()


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _override_session_factory is not None:
        return _override_session_factory
    try:
        return _get_active_session_factory()
    except Exception:
        engine = get_engine()
        return get_session_factory(engine)


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
        logger.debug("Redis client unavailable for triage: %s", exc)
        return None


def _get_registry() -> ScenarioRegistry:
    if _override_registry is not None:
        return _override_registry
    return get_default_scenario_registry()


def _get_policy_service() -> AutopilotPolicyService:
    if _override_policy_service is not None:
        return _override_policy_service
    return AutopilotPolicyService()


@broker.task(task_name="triage_task", queue_name=QUEUE_DEFAULT)
async def triage_task(task_id: int) -> Dict[str, Any]:
    """Execute triage analysis, anti-loop filtering, and gateway checks for a ticket."""
    logger.info("Executing triage_task for task #%d", task_id)

    client = _get_client()
    session_factory = _get_session_factory()
    service_auth = _get_service_auth()
    redis_conn = _get_redis()
    anti_loop = AntiLoopGuard()
    gateway = RelevanceGateway()

    # 1. Authenticate service bot
    auth: ServiceAuthCredentials = await service_auth.bootstrap_auth(
        client=client,
        redis_client=redis_conn,
    )

    # 2. Fetch fresh ticket state
    task: TaskDTO = await client.get_task(task_id=task_id, auth_b64=auth.auth_b64)

    # -------------------------------------------------------------
    # 3. Anti-Loop Guard: check for auto-reply / bounce loop
    # -------------------------------------------------------------
    if anti_loop.is_auto_reply(text=task.description, subject=task.name):
        logger.warning(
            "Ticket #%d detected as automated email robot response. Skipping triage to prevent ping-pong.",
            task_id,
        )
        return {
            "status": "skipped",
            "reason": "auto_reply_detected",
            "task_id": task_id,
        }

    # -------------------------------------------------------------
    # 4. Optimistic Lock (Read-before-Write)
    # -------------------------------------------------------------
    # 4.1. Terminal status check (3=Выполнена, 4=Закрыта, 30=Отменена)
    if task.status_id in (3, 4, 30):
        logger.info(
            "Ticket #%d is already in terminal status %d (%s). Aborting triage.",
            task.id,
            task.status_id,
            task.status_name,
        )
        return {
            "status": "skipped",
            "reason": "already_closed",
            "task_id": task.id,
            "status_id": task.status_id,
        }

    # 4.2. Human engineer assignment check
    executor_ids = task.get_executor_ids()
    human_executors = [
        eid for eid in executor_ids if auth.bot_user_id is None or eid != auth.bot_user_id
    ]
    if human_executors:
        logger.info(
            "Ticket #%d is already assigned to human engineer(s): %s. Aborting auto-triage.",
            task.id,
            human_executors,
        )
        return {
            "status": "skipped",
            "reason": "assigned_to_human",
            "task_id": task.id,
            "executor_ids": task.executor_ids,
        }

    # 4.3. Status 'В работе' (2) without bot assignment
    if task.status_id == 2 and (auth.bot_user_id is None or auth.bot_user_id not in executor_ids):
        logger.info(
            "Ticket #%d is already in progress by an engineer (Status 2). Aborting auto-triage.",
            task.id,
        )
        return {
            "status": "skipped",
            "reason": "in_progress_by_engineer",
            "task_id": task.id,
        }

    # -------------------------------------------------------------
    # 5. Deterministic Relevance Gateway (Filter #1)
    # -------------------------------------------------------------
    decision: RelevanceDecision = gateway.evaluate(task)

    context_snapshot = {
        "id": task.id,
        "name": task.name,
        "service_id": task.service_id,
        "service_name": task.service_name,
        "status_id": task.status_id,
        "status_name": task.status_name,
        "entities": task.entities.model_dump(),
    }

    if decision.is_irrelevant:
        # Step 5.1: Cancel ticket and post public redirect comment
        await client.update_task(
            task_id=task.id,
            status_id=30,  # 30 = Отменена
            comment=decision.public_comment,
            is_private=False,
            auth_b64=auth.auth_b64,
        )

        # Step 5.2: Post hidden internal technical audit note
        await client.update_task(
            task_id=task.id,
            comment=decision.internal_note,
            is_private=True,
            auth_b64=auth.auth_b64,
        )

        # Step 5.3: Persist immutable audit log in PostgreSQL
        async with session_factory() as session:
            audit = TriageAudit(
                task_id=task.id,
                action="cancel_irrelevant",
                model_used="deterministic_gateway",
                confidence=1.0,
                prompt_tokens=0,
                completion_tokens=0,
                context_snapshot=context_snapshot,
                decision_json=decision.model_dump(),
                applied=True,
                applied_by="gateway",
            )
            session.add(audit)
            await session.commit()

        logger.info(
            "Ticket #%d successfully canceled and redirected to %s by Relevance Gateway.",
            task.id,
            decision.target_service_name,
        )
        return {
            "status": "canceled",
            "reason": decision.reason,
            "task_id": task.id,
            "target_service_id": decision.target_service_id,
            "target_service_name": decision.target_service_name,
        }

    # -------------------------------------------------------------
    # 6. Eligible ticket passed Filter #1: Scenario Routing & Dispatch
    # -------------------------------------------------------------
    registry = _get_registry()
    policy_service = _get_policy_service()

    routed = await registry.match_scenario(task)
    scenario = routed[0] if routed else None
    match = routed[1] if routed else None
    confidence = match.confidence if match else 0.0
    scenario_key = scenario.scenario_key if scenario else None

    # Check policy if scenario was matched
    policy = None
    if scenario_key:
        policy = await policy_service.get_policy(scenario_key)

    is_full_auto = (
        policy is not None
        and policy.mode == "FULL_AUTO"
        and confidence >= policy.min_confidence
    )

    if is_full_auto:
        # Step 6.1: Transition ticket to Bot in FULL_AUTO
        # New (1) or Open (31) -> In Work (2)
        new_status_id = 2 if task.status_id in (1, 31) else None
        bot_executor = str(auth.bot_user_id) if auth.bot_user_id is not None else None

        comment_text = (
            f"🤖 [Автопилот] Заявка принята в автоматическую обработку сценарием «{scenario.name}» "
            f"(уверенность: {confidence:.2f})."
        )
        try:
            await client.update_task(
                task_id=task.id,
                status_id=new_status_id,
                executor_ids=bot_executor,
                comment=comment_text,
                is_private=True,
                auth_b64=auth.auth_b64,
            )
        except Exception as exc:
            logger.warning("Failed to assign ticket #%d to bot in IntraService: %s", task.id, exc)

        # Step 6.2: Record auto_assigned_full_auto audit in PostgreSQL
        async with session_factory() as session:
            audit = TriageAudit(
                task_id=task.id,
                action="auto_assigned_full_auto",
                model_used="scenario_router",
                confidence=confidence,
                prompt_tokens=0,
                completion_tokens=0,
                context_snapshot=context_snapshot,
                decision_json={
                    "scenario": scenario_key,
                    "confidence": confidence,
                    "mode": policy.mode,
                    "reasons": match.reasons if match else [],
                },
                applied=True,
                applied_by="autopilot",
            )
            session.add(audit)
            await session.commit()

        logger.info(
            "Ticket #%d auto-assigned to bot in FULL_AUTO mode (scenario: %s, confidence: %.2f)",
            task.id,
            scenario_key,
            confidence,
        )

        # Step 6.3: Dispatch to autopilot_task for execution
        try:
            from worker.src.tasks.autopilot import autopilot_task

            await autopilot_task.kiq(task_id=task.id)
        except Exception as exc:
            logger.error("Failed to enqueue ticket #%d in autopilot_task: %s", task.id, exc)

        return {
            "status": "auto_assigned_full_auto",
            "scenario": scenario_key,
            "confidence": confidence,
            "task_id": task.id,
        }

    # Step 6.4: Fallback for ASSISTED / DISABLED / Low Confidence tickets
    async with session_factory() as session:
        audit = TriageAudit(
            task_id=task.id,
            action="passed_gateway",
            model_used="scenario_router" if scenario else "deterministic_gateway",
            confidence=confidence if scenario else 1.0,
            prompt_tokens=0,
            completion_tokens=0,
            context_snapshot=context_snapshot,
            decision_json={
                "scenario": scenario_key,
                "confidence": confidence,
                "mode": policy.mode if policy else "ASSISTED",
                "reasons": match.reasons if match else [],
            },
            applied=False,
            applied_by="gateway",
        )
        session.add(audit)
        await session.commit()

    logger.info(
        "Ticket #%d passed Filter #1 (Relevance Gateway). Mode: %s. Prepared for Copilot / Engineer review.",
        task.id,
        policy.mode if policy else "ASSISTED",
    )

    # Enqueue background plan prefetch for 0 ms delivery in UI (Copilot)
    try:
        from worker.src.tasks.plan_prefetch import prefetch_agent_plan_task

        await prefetch_agent_plan_task.kiq(ticket_id=task.id)
    except Exception as exc:
        logger.debug("Failed to dispatch plan prefetch task for ticket #%d: %s", task.id, exc)

    return {
        "status": "passed_gateway",
        "reason": decision.reason,
        "scenario": scenario_key,
        "confidence": confidence,
        "task_id": task.id,
    }
