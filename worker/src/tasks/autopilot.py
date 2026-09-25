"""Background autopilot execution task with Autonomous Dialogue Loop and Self-Healing Circuit Breaker.

Enforces:
1. Anti-Loop Guard: suppresses auto-replies, bounces and bot loops.
2. In-Flight Task Concurrency Lock: Redis-based distributed lock preventing worker race conditions.
3. Optimistic Lock: verifies ticket is open and not reassigned to human engineers.
4. Autonomous Dialogue Loop:
   - Suspends ticket (Status 6) with polite instructions if details or host are missing.
   - Resumes execution upon receiving applicant comments (up to 2 clarification rounds).
   - UserReplyIntentAnalyzer: cancels upon request (Status 30), guides on how to find facts,
     and rejects non-corporate home subnets (192.168.x.x / 127.0.0.1).
   - Escalates to human engineers (Status 2) if dialogue limit is reached, home barrier persists,
     or only attachment photos are uploaded.
5. Governance Matrix & Self-Healing Circuit Breaker:
   - FULL_AUTO: autonomous resolution and closure (Status 3).
   - ASSISTED: prepares ActionDock commands without direct closure.
   - Status Transit Safeguard: safe transit 6 -> 2 -> 3 avoiding strict workflow violations.
   - Trips from FULL_AUTO to ASSISTED on 3 consecutive failures within 10 minutes.
6. Zero Black-Box transparency:
   - Posts user-friendly public resolutions to applicants.
   - Posts hidden internal audit notes (`IsPrivateComment=True`) for Helpdesk staff.
"""

import logging
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.autopilot.dto import AutopilotPolicyDTO
from core.autopilot.policy_service import AutopilotPolicyService, get_policy_service
from core.database.models import CommandRecord
from core.database.session import get_engine, get_session_factory
from core.database.system_state import _get_active_session_factory
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO
from core.redis_client import get_redis_client
from worker.src.broker import QUEUE_DEFAULT, broker
from core.scenarios.base import BaseScenario
from core.scenarios.orchestrator import ScenarioLifecycleOrchestrator
from core.scenarios.registry import ScenarioRegistry, get_default_scenario_registry
from core.autopilot.dialogue import AntiLoopGuard, UserReplyIntent, UserReplyIntentAnalyzer
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials

logger = logging.getLogger("worker.tasks.autopilot")

# Test override hooks
_override_client: Optional[IntraServiceClient] = None
_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_override_service_auth: Optional[ServiceAuthBootstrap] = None
_override_redis_client: Optional[aioredis.Redis] = None
_override_policy_service: Optional[AutopilotPolicyService] = None
_override_registry: Optional[ScenarioRegistry] = None


def set_autopilot_client(client: Optional[IntraServiceClient]) -> None:
    global _override_client
    _override_client = client


def set_autopilot_session_factory(factory: Optional[async_sessionmaker[AsyncSession]]) -> None:
    global _override_session_factory
    _override_session_factory = factory


def set_autopilot_service_auth(auth_bootstrap: Optional[ServiceAuthBootstrap]) -> None:
    global _override_service_auth
    _override_service_auth = auth_bootstrap


def set_autopilot_redis_client(redis_conn: Optional[aioredis.Redis]) -> None:
    global _override_redis_client
    _override_redis_client = redis_conn


def set_autopilot_policy_service(service: Optional[AutopilotPolicyService]) -> None:
    global _override_policy_service
    _override_policy_service = service


def set_autopilot_registry(registry: Optional[ScenarioRegistry]) -> None:
    global _override_registry
    _override_registry = registry


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
        logger.debug("Redis client unavailable for autopilot: %s", exc)
        return None


def _get_policy_service() -> AutopilotPolicyService:
    if _override_policy_service is not None:
        return _override_policy_service
    return get_policy_service()


def _get_registry() -> ScenarioRegistry:
    if _override_registry is not None:
        return _override_registry
    return get_default_scenario_registry()


def _count_clarification_rounds(
    lifetimes: List[TaskLifetimeEventDTO],
    bot_user_id: Optional[int],
) -> int:
    """Count how many times autopilot suspended the ticket or requested clarification."""
    rounds = 0
    for event in lifetimes:
        # Check if ticket was suspended to Status 6
        if event.status_id == 6:
            rounds += 1
            continue
        # Check if bot posted a clarification comment
        if bot_user_id is not None and event.editor_id == bot_user_id:
            text = event.comment or ""
            if "Здравствуйте! Для" in text or "Пожалуйста, включите компьютер" in text:
                rounds += 1
    return rounds


@broker.task(task_name="autopilot_task", queue_name=QUEUE_DEFAULT)
async def autopilot_task(task_id: int) -> Dict[str, Any]:
    """Execute autonomous scenario workflow for a ticket assigned to service bot."""
    logger.info("Executing autopilot_task for task #%d", task_id)

    client = _get_client()
    session_factory = _get_session_factory()
    service_auth = _get_service_auth()
    redis_conn = _get_redis()
    policy_service = _get_policy_service()
    registry = _get_registry()
    anti_loop = AntiLoopGuard()
    intent_analyzer = UserReplyIntentAnalyzer()

    # 0. Cooperative Cancellation check
    abort_key = f"autopilot:abort:{task_id}"
    if redis_conn is not None:
        try:
            if await redis_conn.exists(abort_key):
                logger.info("Ticket #%d was reclaimed by human operator (abort flag active). Skipping autopilot.", task_id)
                return {"status": "aborted", "reason": "reclaimed_by_operator", "task_id": task_id}
        except Exception as exc:
            logger.debug("Redis abort check error for ticket #%d: %s", task_id, exc)

    # 0.1. Distributed Concurrency Lock: only one worker processes task_id at a time
    lock_key = f"lock:task:{task_id}"
    lock_key_legacy = f"lock:autopilot:{task_id}"
    lock_acquired = False
    if redis_conn is not None:
        try:
            acquired_canonical = bool(await redis_conn.set(lock_key, "locked", nx=True, ex=60))
            acquired_legacy = bool(await redis_conn.set(lock_key_legacy, "locked", nx=True, ex=60))
            if not acquired_canonical or not acquired_legacy:
                if acquired_canonical:
                    await redis_conn.delete(lock_key)
                if acquired_legacy:
                    await redis_conn.delete(lock_key_legacy)
                logger.info("Ticket #%d is already in-flight by another worker. Skipping concurrent execution.", task_id)
                return {"status": "skipped", "reason": "concurrent_lock_active", "task_id": task_id}
            lock_acquired = True
        except Exception as exc:
            logger.debug("Redis lock error for ticket #%d: %s", task_id, exc)

    try:
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
            logger.warning("Ticket #%d detected as automated email robot response. Skipping autopilot.", task_id)
            return {"status": "skipped", "reason": "auto_reply_detected", "task_id": task_id}

        # -------------------------------------------------------------
        # 4. Optimistic Lock (Read-before-Write)
        # -------------------------------------------------------------
        # 4.1. Terminal status check (3=Выполнена, 4=Закрыта, 30=Отменена)
        if task.status_id in (3, 4, 30):
            logger.info("Ticket #%d is already in terminal status %d (%s). Aborting autopilot.", task.id, task.status_id, task.status_name)
            return {"status": "skipped", "reason": "already_closed", "task_id": task.id, "status_id": task.status_id}

        # 4.2. Human engineer assignment check
        executor_ids = task.get_executor_ids()
        if auth.bot_user_id is not None and auth.bot_user_id not in executor_ids and executor_ids:
            logger.info("Ticket #%d is reassigned to human engineer(s): %s. Aborting autopilot.", task.id, executor_ids)
            return {"status": "skipped", "reason": "assigned_to_human", "task_id": task.id, "executor_ids": task.executor_ids}

        # -------------------------------------------------------------
        # 5. Fetch lifetime history & resume dialogue loop
        # -------------------------------------------------------------
        lifetimes: List[TaskLifetimeEventDTO] = await client.get_task_lifetime(task_id=task.id, auth_b64=auth.auth_b64)
        clarification_rounds = anti_loop.count_clarification_rounds(lifetimes, auth.bot_user_id)

        applicant_comments = [
            e for e in lifetimes
            if e.comment and (auth.bot_user_id is None or e.editor_id != auth.bot_user_id) and not e.is_private
        ]
        if applicant_comments:
            latest_reply = applicant_comments[-1].comment or ""
            # Anti-Loop check: email robot auto-reply (out of office / vacation notice)
            if anti_loop.is_auto_reply(text=latest_reply):
                logger.warning("Applicant comment in ticket #%d is an automated out-of-office bounce response. Skipping.", task.id)
                return {"status": "skipped", "reason": "auto_reply_in_dialogue", "task_id": task.id}

            intent_res = intent_analyzer.analyze_reply(
                text=latest_reply,
                has_new_attachments=bool(task.attachments),
            )

            # 5.1. Applicant asks to cancel or resolved issue themselves
            if intent_res.intent == UserReplyIntent.CANCEL_REQUEST:
                logger.info("Applicant requested cancellation for ticket #%d: %s", task.id, intent_res.summary)
                await client.update_task(
                    task_id=task.id,
                    status_id=30,  # Отменена
                    comment="Здравствуйте! Заявка отменена по вашей просьбе. Рады, что вопрос решился!",
                    is_private=False,
                    auth_b64=auth.auth_b64,
                )
                await client.update_task(
                    task_id=task.id,
                    comment=f"🤖 [Автопилот: Отмена по просьбе заявителя]\nИнтент: {intent_res.summary}\nСтатус переведен в 30 (Отменена).",
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {"status": "canceled_by_applicant", "task_id": task.id}

            # 5.2. Applicant asks where to find network credentials
            if intent_res.intent == UserReplyIntent.CLARIFICATION_QUESTION:
                logger.info("Applicant requested guidance for ticket #%d.", task.id)
                await client.update_task(
                    task_id=task.id,
                    comment=intent_res.suggested_reply or "",
                    is_private=False,
                    auth_b64=auth.auth_b64,
                )
                await client.update_task(
                    task_id=task.id,
                    comment="🤖 [Автопилот: Инструкция заявителю]\nОтправлены подсказки по поиску наклейки ПК и IP принтера.",
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {"status": "helpful_hint_sent", "task_id": task.id}

            # 5.3. Applicant provided a home/loopback IP (192.168.x.x / 127.0.0.1)
            if intent_res.intent == UserReplyIntent.SUBNET_MISMATCH:
                logger.warning("Applicant provided home subnet IP for ticket #%d: %s", task.id, intent_res.invalid_ip)
                await client.update_task(
                    task_id=task.id,
                    comment=intent_res.suggested_reply or "",
                    is_private=False,
                    auth_b64=auth.auth_b64,
                )
                await client.update_task(
                    task_id=task.id,
                    comment=f"🤖 [Автопилот: Отклонен домашний IP]\nАдрес {intent_res.invalid_ip} не принадлежит корпоративной сети 10.x.x.x.",
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {"status": "home_subnet_rejected", "task_id": task.id, "ip": intent_res.invalid_ip}

            # 5.4. Applicant uploaded photo of sticker or screenshot without text
            if intent_res.intent == UserReplyIntent.ATTACHMENTS_ONLY:
                logger.info("Applicant uploaded attachment without text for ticket #%d. Escalating to human.", task.id)
                await client.update_task(
                    task_id=task.id,
                    status_id=2,  # В работе
                    comment="🤖 [Автопилот: Вложение от заявителя]\nЗаявитель прикрепил файл/скриншот с реквизитами. Передано инженеру для визуального осмотра.",
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {"status": "escalated_attachments_only", "task_id": task.id}

            # 5.5. Credentials successfully extracted
            if intent_res.intent == UserReplyIntent.PROVIDE_DATA:
                enriched = intent_res.extracted_entities
                if not task.entities.pc_name and enriched.pc_name:
                    task.entities.pc_name = enriched.pc_name
                if not task.entities.printer_address and enriched.printer_address:
                    task.entities.printer_address = enriched.printer_address
                if not task.entities.printer_model and enriched.printer_model:
                    task.entities.printer_model = enriched.printer_model
                if not task.entities.target_user and enriched.target_user:
                    task.entities.target_user = enriched.target_user

        # -------------------------------------------------------------
        # 6. Scenario Resolution
        # -------------------------------------------------------------
        scenario: Optional[BaseScenario] = await registry.find_scenario(task)
        if scenario is None:
            logger.info("No matching autopilot scenario found for ticket #%d. Escalating to human.", task.id)
            await client.update_task(
                task_id=task.id,
                status_id=2,  # В работе
                comment=(
                    "🤖 [Автопилот: Сценарий не определен]\n"
                    "Заявка не соответствует ни одному из активных сценариев автопилота.\n"
                    "Передана на ручную обработку инженеру 1-й линии."
                ),
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {"status": "unmatched", "task_id": task.id}

        # -------------------------------------------------------------
        # 7. Autopilot Policy Evaluation (Governance & Circuit Breaker)
        # -------------------------------------------------------------
        policy: AutopilotPolicyDTO = await policy_service.get_policy(scenario.scenario_key)

        if policy.mode == "DISABLED":
            logger.info("Autopilot scenario '%s' is DISABLED by policy. Skipping.", scenario.scenario_key)
            return {"status": "disabled", "scenario": scenario.scenario_key, "task_id": task.id}

        if policy.mode == "ASSISTED":
            logger.info("Scenario '%s' running in ASSISTED mode. Preparing command record.", scenario.scenario_key)
            precond = await scenario.validate_preconditions(task)
            async with session_factory() as session:
                cmd = CommandRecord(
                    idempotency_key=f"assisted_{task.id}_{scenario.scenario_key}",
                    action=scenario.scenario_key,
                    executor="worker",
                    target_json={"task_id": task.id, "pc_name": task.entities.pc_name},
                    params_json={"entities": task.entities.model_dump(), "preconditions": precond.model_dump()},
                    status="pending",
                    initiator="autopilot_assisted",
                    task_id=task.id,
                )
                session.add(cmd)
                try:
                    await session.commit()
                except Exception as exc:
                    logger.debug("CommandRecord already exists for assisted task #%d: %s", task.id, exc)

            await client.update_task(
                task_id=task.id,
                comment=(
                    f"🤖 [Автопилот: Режим Ко-пилота (ASSISTED)]\n"
                    f"Сценарий '{scenario.name}' подготовлен.\n"
                    f"Параметры зафиксированы в ActionDock. Ожидает действия инженера."
                ),
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {"status": "assisted_prepared", "scenario": scenario.scenario_key, "task_id": task.id}

        # -------------------------------------------------------------
        # 8. FULL_AUTO Execution & Dialogue Loop
        # -------------------------------------------------------------
        preconditions = await scenario.validate_preconditions(task)

        if not preconditions.is_valid:
            # 8.1. Attachment Heuristic (Edge Case 6):
            # Если реквизитов не хватает, но в тикете есть прикрепленные файлы (сканы, PDF, фото стикеров) -
            # автопилот НЕ шлет вопрос заявителю, а сразу передает заявку дежурному инженеру (статус 2)
            if task.attachments:
                logger.info(
                    "Ticket #%d is missing facts (%s) but has %d attachment(s). Escalating to human for visual inspection.",
                    task.id,
                    preconditions.missing_facts,
                    len(task.attachments),
                )
                await client.update_task(
                    task_id=task.id,
                    status_id=2,  # В работе
                    comment=(
                        f"🤖 [Автопилот: Требуется визуальный осмотр вложений]\n"
                        f"Недостающие реквизиты: {', '.join(preconditions.missing_facts) if preconditions.missing_facts else 'Барьеры окружения'}.\n"
                        f"В заявке обнаружены прикрепленные файлы ({len(task.attachments)} шт.). "
                        "Заявка передана дежурному инженеру для анализа сканов/фотографий без отправки повторного вопроса заявителю."
                    ),
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {
                    "status": "escalated_attachments_present",
                    "task_id": task.id,
                    "scenario": scenario.scenario_key,
                    "attachments_count": len(task.attachments),
                    "missing_facts": preconditions.missing_facts,
                }

            # 8.2. Dialogue turn limit
            if clarification_rounds >= 2:
                logger.warning("Ticket #%d exceeded clarification limit (%d rounds). Escalating to human.", task.id, clarification_rounds)
                await client.update_task(
                    task_id=task.id,
                    status_id=2,  # В работе
                    comment=(
                        f"🤖 [Автопилот: Превышен лимит диалога]\n"
                        f"Заявитель не предоставил необходимые данные за {clarification_rounds} раунда уточнений.\n"
                        "Автопилот завершил попытки и передал заявку инженеру 1-й линии."
                    ),
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {
                    "status": "escalated_dialogue_limit",
                    "task_id": task.id,
                    "scenario": scenario.scenario_key,
                    "rounds": clarification_rounds,
                }

            # 8.3. Resolve clarification prompt: from preconditions or service definition
            prompt = preconditions.clarification_prompt
            if not prompt and scenario.definition and scenario.definition.clarification_template:
                prompt = scenario.definition.clarification_template

            if prompt:
                logger.info("Suspending ticket #%d (Status 6) with prompt: %s", task.id, prompt)
                await client.update_task(
                    task_id=task.id,
                    status_id=6,
                    comment=prompt,
                    is_private=False,
                    auth_b64=auth.auth_b64,
                )
                await client.update_task(
                    task_id=task.id,
                    comment=(
                        f"🤖 [Автопилот: Запрос уточнения (раунд {clarification_rounds + 1})]\n"
                        f"Отсутствующие реквизиты: {', '.join(preconditions.missing_facts) if preconditions.missing_facts else 'Барьеры окружения'}\n"
                        f"Барьеры среды: {', '.join(preconditions.environment_barriers)}\n"
                        "Статус переведен в 6 (Приостановлена / Ожидание ответа)."
                    ),
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {
                    "status": "paused_waiting_applicant",
                    "task_id": task.id,
                    "round": clarification_rounds + 1,
                    "missing_facts": preconditions.missing_facts,
                    "barriers": preconditions.environment_barriers,
                }

            logger.info("Preconditions failed without clarification prompt for ticket #%d. Escalating to human.", task.id)
            await client.update_task(
                task_id=task.id,
                status_id=2,  # В работе
                comment=(
                    f"🤖 [Автопилот: Недостаточно уверенности]\n"
                    f"Сценарий '{scenario.name}' не набрал требуемого порога уверенности ({policy.min_confidence:.1%}).\n"
                    "Заявка передана на ручную обработку инженеру 1-й линии."
                ),
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {"status": "escalated_low_confidence", "task_id": task.id, "scenario": scenario.scenario_key}

        # -------------------------------------------------------------
        # 9. Scenario Execution & Lifecycle Orchestration
        # -------------------------------------------------------------
        orchestrator = ScenarioLifecycleOrchestrator(
            client=client,
            redis_conn=redis_conn,
            policy_service=policy_service,
        )
        exec_result = await orchestrator.execute_and_audit(
            scenario=scenario,
            task=task,
            policy=policy,
            auth_b64=auth.auth_b64,
            initiator="autopilot",
            update_circuit_breaker=True,
        )

        if exec_result.success:
            logger.info("Scenario '%s' succeeded for ticket #%d. Closing ticket with status %d.", scenario.scenario_key, task.id, exec_result.target_status_id)
            return {
                "status": "resolved",
                "task_id": task.id,
                "scenario": scenario.scenario_key,
                "target_status_id": exec_result.target_status_id,
            }

        return {
            "status": "failed",
            "task_id": task.id,
            "scenario": scenario.scenario_key,
            "error": exec_result.error,
            "circuit_broken": exec_result.metadata.get("circuit_broken", False),
        }
    finally:
        if redis_conn is not None and lock_acquired:
            try:
                await redis_conn.delete(lock_key, lock_key_legacy)
            except Exception:
                pass
