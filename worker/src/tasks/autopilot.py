"""Background autopilot execution task with Autonomous Dialogue Loop and Evidence-Based Routing.

Enforces:
1. Anti-Loop Guard: suppresses auto-replies, bounces and bot loops.
2. In-Flight Task Concurrency Lock: Redis-based fail-closed distributed token lease.
3. Optimistic Lock: verifies ticket is open and not reassigned to human engineers.
4. Autonomous Dialogue Loop:
   - Suspends ticket (Status 6) with polite instructions if details or host are missing.
   - Resumes execution upon receiving applicant comments (up to 2 clarification rounds).
   - UserReplyIntentAnalyzer: cancels upon request (Status 30), guides on how to find facts,
     and rejects non-corporate home subnets (192.168.x.x / 127.0.0.1).
   - Escalates to human engineers (Status 2) if dialogue limit is reached, home barrier persists,
     or only attachment photos are uploaded.
5. Governance Matrix & Single Execution Owner:
   - FULL_AUTO: strictly executes verified PreparedPlanRecord by dispatching CommandRecord to command dispatcher.
   - ASSISTED: prepares ActionDock plans without autonomous dispatch.
   - Single Execution Owner: command_dispatcher is the exclusive scenario mutation executor.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.autopilot.dialogue import AntiLoopGuard, UserReplyIntent, UserReplyIntentAnalyzer
from core.autopilot.policy_service import AutopilotPolicyService, get_policy_service
from core.database.models import (
    CommandRecord,
    PreparedPlanRecord,
    RoutingFeedbackRecord,
    RoutingPreflightRecord,
    sanitize_secrets,
)
from core.database.session import get_engine, get_session_factory
from core.database.system_state import _get_active_session_factory
from core.intraservice.auth import ServiceAuthBootstrap, ServiceAuthCredentials
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO
from core.redis_client import get_redis_client
from core.redis_lock import DistributedTaskLock
from core.routing.observability import emit_routing_event
from core.routing.preflight import (
    compute_canonical_params_hash,
    compute_canonical_plan_hash_from_json,
    is_executable_preflight_status,
)
from core.routing.snapshot import build_ticket_snapshot, compute_snapshot_hash
from core.scenarios.registry import ScenarioRegistry, get_default_scenario_registry
from worker.src.broker import QUEUE_DEFAULT, broker

logger = logging.getLogger("worker.tasks.autopilot")

# Test override hooks
_override_client: Optional[IntraServiceClient] = None
_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_override_service_auth: Optional[ServiceAuthBootstrap] = None
_override_redis_client: Optional[aioredis.Redis] = None
_override_policy_service: Optional[AutopilotPolicyService] = None
_override_registry: Optional[ScenarioRegistry] = None
_override_full_auto_enabled: Optional[bool] = None


def set_full_auto_feature_gate(enabled: Optional[bool]) -> None:
    """Override FULL_AUTO feature gate (disabled by default in v2 runtime)."""
    global _override_full_auto_enabled
    _override_full_auto_enabled = enabled


def _is_full_auto_enabled() -> bool:
    if _override_full_auto_enabled is not None:
        return _override_full_auto_enabled
    return False


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


@broker.task(task_name="autopilot_task", queue_name=QUEUE_DEFAULT)
async def autopilot_task(task_id: int) -> Dict[str, Any]:
    """Execute autonomous scenario workflow for a ticket assigned to service bot."""
    logger.info("Executing autopilot_task for task #%d", task_id)

    client = _get_client()
    session_factory = _get_session_factory()
    service_auth = _get_service_auth()
    redis_conn = _get_redis()
    policy_service = _get_policy_service()
    anti_loop = AntiLoopGuard()
    intent_analyzer = UserReplyIntentAnalyzer()

    # 0. Cooperative Cancellation check in Redis
    abort_key = f"autopilot:abort:{task_id}"
    if redis_conn is not None:
        try:
            if await redis_conn.exists(abort_key):
                logger.info("Ticket #%d was reclaimed by human operator (abort flag active). Skipping autopilot.", task_id)
                return {"status": "aborted", "reason": "reclaimed_by_operator", "task_id": task_id}
        except Exception as exc:
            logger.debug("Redis abort check error for ticket #%d: %s", task_id, exc)

    # 0.1. Durable PostgreSQL cancellation check
    async with session_factory() as session:
        stmt_terminal = (
            select(RoutingFeedbackRecord)
            .where(
                RoutingFeedbackRecord.task_id == task_id,
                RoutingFeedbackRecord.verdict.in_(["rejected", "manual_takeover"]),
            )
            .order_by(desc(RoutingFeedbackRecord.created_at))
            .limit(1)
        )
        terminal_fb = (await session.execute(stmt_terminal)).scalar_one_or_none()
        if terminal_fb is not None:
            logger.info("Ticket #%d has terminal feedback '%s'. Aborting autopilot.", task_id, terminal_fb.verdict)
            return {"status": "aborted", "reason": f"terminal_feedback_{terminal_fb.verdict}", "task_id": task_id}

        stmt_pending = (
            select(CommandRecord)
            .where(CommandRecord.task_id == task_id, CommandRecord.status == "pending")
            .order_by(desc(CommandRecord.created_at))
            .limit(1)
        )
        approved_cmd = (await session.execute(stmt_pending)).scalar_one_or_none()
        stmt_latest_plan = (
            select(PreparedPlanRecord)
            .where(PreparedPlanRecord.task_id == task_id)
            .order_by(desc(PreparedPlanRecord.created_at))
            .limit(1)
        )
        latest_plan = (await session.execute(stmt_latest_plan)).scalar_one_or_none()

    # Read-only guards do not require an execution lease.
    auth: ServiceAuthCredentials = await service_auth.bootstrap_auth(
        client=client,
        redis_client=redis_conn,
    )
    task: TaskDTO = await client.get_task(task_id=task_id, auth_b64=auth.auth_b64)
    if anti_loop.is_auto_reply(text=task.description, subject=task.name):
        return {"status": "skipped", "reason": "auto_reply_detected", "task_id": task_id}
    if task.status_id in (3, 4, 30):
        return {"status": "skipped", "reason": "already_closed", "task_id": task.id, "status_id": task.status_id}
    executor_ids = task.get_executor_ids()
    if auth.bot_user_id is not None and auth.bot_user_id not in executor_ids and executor_ids:
        return {"status": "skipped", "reason": "assigned_to_human", "task_id": task.id, "executor_ids": task.executor_ids}

    # ASSISTED is a strict non-mutating boundary. Approved commands are owned by
    # command_dispatcher; mere assignment to the bot must not require Redis or
    # trigger dialogue/status changes.
    if approved_cmd is not None:
        return {
            "status": "command_already_dispatched",
            "command_id": str(approved_cmd.id),
            "task_id": task_id,
        }
    if not _is_full_auto_enabled():
        return {
            "status": "awaiting_operator_approval",
            "task_id": task_id,
            "message": "Ожидает подтверждения плана оператором",
        }

    # Only autonomous planning/dialogue owns this lease. The dispatcher obtains
    # a separate lease after the durable handoff.
    lock = DistributedTaskLock(redis_conn, f"lock:task:{task_id}", ttl_seconds=60)
    lock_acquired = await lock.acquire()
    if not lock_acquired:
        return {"status": "skipped", "reason": "concurrent_lock_active_or_unavailable", "task_id": task_id}

    try:

        # -------------------------------------------------------------
        # 5. Fetch lifetime history & resume dialogue loop
        # -------------------------------------------------------------
        lifetimes: List[TaskLifetimeEventDTO] = await client.get_task_lifetime(task_id=task.id, auth_b64=auth.auth_b64)
        clarification_rounds = anti_loop.count_clarification_rounds(lifetimes, auth.bot_user_id)

        # Even with the global feature gate enabled, an existing ASSISTED plan
        # must never enter the autonomous dialogue mutation path.
        dialogue_enabled = False
        if latest_plan is not None:
            async with session_factory() as session:
                latest_policy = await policy_service.get_policy(latest_plan.scenario_key, session=session)
            if latest_policy.mode != "FULL_AUTO" or latest_policy.is_circuit_broken:
                return {
                    "status": "awaiting_operator_approval",
                    "task_id": task.id,
                    "scenario": latest_plan.scenario_key,
                    "policy_mode": latest_policy.mode,
                }
            dialogue_enabled = True

        applicant_comments = [
            e for e in lifetimes
            if e.comment and (auth.bot_user_id is None or e.editor_id != auth.bot_user_id) and not e.is_private
        ] if dialogue_enabled else []
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
        # 7. Strict FULL_AUTO: Evidence Cascade & PreparedPlanRecord Validation
        # -------------------------------------------------------------
        current_snapshot = build_ticket_snapshot(task, comments=lifetimes)
        current_snapshot_hash = compute_snapshot_hash(current_snapshot)

        async with session_factory() as session:
            stmt_plan = (
                select(PreparedPlanRecord)
                .where(PreparedPlanRecord.task_id == task.id)
                .order_by(desc(PreparedPlanRecord.created_at))
                .limit(1)
            )
            plan_rec = (await session.execute(stmt_plan)).scalar_one_or_none()

        # If no plan exists or plan is stale relative to fresh snapshot, run Evidence Cascade on-demand
        if plan_rec is None or plan_rec.snapshot_hash != current_snapshot_hash:
            from core.routing.analysis_service import TicketAnalysisService
            analysis_svc = TicketAnalysisService(client=client, policy_service=policy_service, session_factory=session_factory)
            try:
                async with session_factory() as session:
                    await analysis_svc.analyze_ticket(
                        ticket_id=task.id,
                        session=session,
                        redis_client=redis_conn,
                        auth_b64=auth.auth_b64,
                    )
                async with session_factory() as session:
                    plan_rec = (await session.execute(stmt_plan)).scalar_one_or_none()
            except Exception as exc:
                logger.warning("On-demand cascade analysis failed for ticket #%d: %s", task.id, exc)

        if plan_rec is None:
            await client.update_task(
                task_id=task.id,
                status_id=2,  # В работе
                comment="🤖 [Автопилот: Сценарий не определен]\nЗаявка не соответствует автоматическим сценариям. Передана инженеру.",
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {"status": "unmatched", "task_id": task.id}

        # Validate fresh snapshot binding
        if plan_rec.snapshot_hash != current_snapshot_hash:
            logger.warning("PreparedPlan %s snapshot_hash mismatch with fresh ticket #%d. Fail-closed.", plan_rec.id, task.id)
            await client.update_task(
                task_id=task.id,
                status_id=2,
                comment="🤖 [Автопилот: Изменение заявки]\nДанные заявки изменились в процессе обработки. План аннулирован и передан инженеру.",
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {"status": "stale_plan_snapshot_mismatch", "task_id": task.id}

        plan_data = plan_rec.plan_json or {}
        try:
            canonical_plan_hash = compute_canonical_plan_hash_from_json(plan_data)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("PreparedPlan %s has invalid canonical payload: %s", plan_rec.id, exc)
            return {"status": "invalid_plan_binding", "reason": "plan_tampered", "task_id": task.id}
        if canonical_plan_hash != plan_rec.plan_hash:
            logger.warning("PreparedPlan %s canonical hash mismatch. Fail-closed.", plan_rec.id)
            return {"status": "invalid_plan_binding", "reason": "plan_hash_mismatch", "task_id": task.id}

        # Policy is checked before every autonomous dialogue or ticket mutation.
        async with session_factory() as session:
            policy = await policy_service.get_policy(plan_rec.scenario_key, session=session)
        if policy.mode != "FULL_AUTO" or policy.is_circuit_broken:
            return {
                "status": "awaiting_operator_approval",
                "task_id": task.id,
                "scenario": plan_rec.scenario_key,
                "policy_mode": policy.mode,
            }

        # Check dialogue clarification state
        if plan_rec.state == "needs_clarification":
            # Check attachments edge case
            if task.attachments:
                logger.info("Ticket #%d has missing facts but attachments are present. Escalating to human.", task.id)
                await client.update_task(
                    task_id=task.id,
                    status_id=2,
                    comment="🤖 [Автопилот: Вложение от заявителя]\nЗаявитель прикрепил вложение. Требуется визуальный осмотр вложений инженером.",
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {"status": "escalated_attachments_present", "task_id": task.id, "attachments_count": len(task.attachments)}

            if clarification_rounds >= 2:
                logger.warning("Ticket #%d reached max clarification limit (%d rounds). Escalating.", task.id, clarification_rounds)
                await client.update_task(
                    task_id=task.id,
                    status_id=2,
                    comment="🤖 [Автопилот: Превышен лимит диалога]\nЗаявитель не предоставил необходимые данные за 2 раунда уточнения. Передано инженеру.",
                    is_private=True,
                    auth_b64=auth.auth_b64,
                )
                return {"status": "escalated_dialogue_limit", "task_id": task.id, "rounds": clarification_rounds}

            # Suspend ticket to Status 6 and prompt applicant
            missing = plan_data.get("missing_facts", ["данные"])
            user_msg = (
                plan_data.get("clarification_prompt")
                or f"Здравствуйте! Для выполнения заявки по сценарию нам требуются дополнительные сведения: {', '.join(missing)}. Пожалуйста, ответьте на это сообщение."
            )
            await client.update_task(
                task_id=task.id,
                status_id=6,  # Приостановлена
                comment=user_msg,
                is_private=False,
                auth_b64=auth.auth_b64,
            )
            await client.update_task(
                task_id=task.id,
                comment=f"🤖 [Автопилот: Запрос уточнения (раунд {clarification_rounds + 1})]\nОтправлен запрос недостающих параметров: {', '.join(missing)}.\nСтатус переведен в 6 (Приостановлена).",
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {
                "status": "paused_waiting_applicant",
                "task_id": task.id,
                "round": clarification_rounds + 1,
                "scenario": plan_rec.scenario_key,
                "missing_facts": missing,
            }

        # Validate executable selected plan
        if plan_rec.state != "selected" or plan_rec.preflight_id is None:
            logger.info("Ticket #%d plan state is '%s' (not executable). Fail-closed.", task.id, plan_rec.state)
            await client.update_task(
                task_id=task.id,
                status_id=2,
                comment="🤖 [Автопилот: Сценарий не определен]\nЗаявка не соответствует условиям автоматического исполнения. Передана инженеру.",
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {"status": "unmatched", "task_id": task.id}

        # Validate Preflight status and TTL
        async with session_factory() as session:
            stmt_pf = select(RoutingPreflightRecord).where(RoutingPreflightRecord.id == plan_rec.preflight_id)
            preflight_rec = (await session.execute(stmt_pf)).scalar_one_or_none()

        proposed_params = sanitize_secrets(plan_data.get("proposed_params", {}))
        expected_params_hash = compute_canonical_params_hash(proposed_params)
        preflight_binding_valid = bool(
            preflight_rec is not None
            and preflight_rec.decision_id == plan_rec.decision_id
            and preflight_rec.task_id == task.id
            and preflight_rec.snapshot_hash == plan_rec.snapshot_hash
            and preflight_rec.scenario_key == plan_rec.scenario_key
            and preflight_rec.params_hash == expected_params_hash
        )
        if not preflight_binding_valid or not is_executable_preflight_status(preflight_rec.status):
            pf_status = preflight_rec.status if preflight_rec else "missing"
            logger.warning("Preflight %s validation failed (%s) for ticket #%d. Fail-closed.", plan_rec.preflight_id, pf_status, task.id)
            await client.update_task(
                task_id=task.id,
                status_id=2,
                comment=f"🤖 [Автопилот: Проверка готовности не пройдена]\nPreflight-статус: {pf_status}. Заявка передана на ручную обработку инженеру.",
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {"status": "preflight_failed", "preflight_status": pf_status, "task_id": task.id}

        # Verify preflight TTL (300 seconds default)
        expires_at = preflight_rec.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and expires_at < datetime.now(timezone.utc):
            logger.warning("Preflight %s expired for ticket #%d. Fail-closed.", plan_rec.preflight_id, task.id)
            await client.update_task(
                task_id=task.id,
                status_id=2,
                comment="🤖 [Автопилот: Preflight устарел]\nИстек срок действия предварительной проверки. Заявка передана инженеру.",
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            return {"status": "preflight_expired", "task_id": task.id}

        # -------------------------------------------------------------
        # 8. Create CommandRecord and dispatch to Command Dispatcher
        # -------------------------------------------------------------
        # Determine executor
        if plan_rec.scenario_key in ("install_printer", "printer_spooler_restart", "default_printer_fix"):
            executor = "windows_exec"
        elif plan_rec.scenario_key in ("service_redirect", "rag_consultation"):
            executor = "api"
        else:
            executor = "intralink_worker"

        async with session_factory() as session:
            cmd = CommandRecord(
                decision_id=plan_rec.decision_id,
                plan_id=plan_rec.id,
                plan_hash=plan_rec.plan_hash,
                action=plan_rec.scenario_key,
                executor=executor,
                params_json=proposed_params,
                target_json={"ticket_id": task.id, "task_id": task.id},
                initiator="full_auto",
                status="pending",
                task_id=task.id,
                idempotency_key=f"full_auto_{task.id}_{plan_rec.plan_hash}",
            )
            session.add(cmd)
            await session.commit()
            await session.refresh(cmd)

        # End the orchestration lease before publishing. The dispatcher owns the
        # execution lease and may start immediately after the broker accepts it.
        await lock.release()
        lock_acquired = False

        from worker.src.tasks.command_dispatcher import dispatch_command_task
        await dispatch_command_task.kiq(str(cmd.id))

        emit_routing_event(
            "full_auto_command_dispatched",
            task_id=task.id,
            decision_id=plan_rec.decision_id,
            plan_id=plan_rec.id,
            command_id=cmd.id,
            scenario_key=plan_rec.scenario_key,
            routing_state="selected",
        )

        return {
            "status": "command_dispatched",
            "task_id": task.id,
            "command_id": str(cmd.id),
            "scenario": plan_rec.scenario_key,
        }

    finally:
        if lock_acquired:
            await lock.release()
