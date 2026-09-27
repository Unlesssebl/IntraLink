"""Autopilot Service for Human-in-the-Loop Supervision and Canonical Routing Feedback.

Orchestrates read-only plan queries, on-demand Evidence Routing analysis,
preflight verification, single-click operator approvals, ground-truth corrections,
and comprehensive routing quality analytics without legacy dual-writes or confidence hacks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.config import settings
from api.src.core.task_dispatch import TaskDispatchService, dispatch_command
from core.autopilot.dto import (
    AgentPlanDTO,
    AutopilotPolicyDTO,
    RoutingFeedbackDTO,
    RoutingQualityMetricsDTO,
)
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.models import (
    CommandRecord,
    PreparedPlanRecord,
    RoutingDecisionRecord,
    RoutingFeedbackRecord,
    RoutingPreflightRecord,
    sanitize_secrets,
)
from core.diagnostic.service import HostDiagnosticsService
from core.intraservice.auth import ServiceAuthBootstrap
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO
from core.intraservice.exceptions import IntraServiceNotFoundError
from core.routing.analysis_service import _SCENARIO_META, TicketAnalysisService
from core.routing.catalog_service import ScenarioCatalogService
from core.routing.observability import emit_routing_event
from core.routing.preflight import (
    EXECUTABLE_PREFLIGHT_STATUSES,
    RoutingPreflightService,
    compute_canonical_params_hash,
    compute_canonical_plan_hash,
    compute_canonical_plan_hash_from_json,
    is_executable_preflight_status,
)
from core.routing.readiness import FactReadinessPolicy
from core.routing.snapshot import TicketSnapshotFactory
from core.scenarios.reporter import sanitize_value

from .schemas import (
    ApprovePlanRequest,
    ApprovePlanResponse,
    BatchAssignResponse,
    CorrectPlanRequest,
    CorrectPlanResponse,
    FeedbackListResponse,
    ManualTakeoverRequest,
    ManualTakeoverResponse,
    RejectPlanRequest,
    RejectPlanResponse,
)

logger = logging.getLogger("api.features.autopilot.service")


class AutopilotService:
    """Service orchestrating agent plan evaluation, supervisor approvals and canonical feedback."""

    def __init__(
        self,
        client: Optional[IntraServiceClient] = None,
        policy_service: Optional[AutopilotPolicyService] = None,
        diagnostics_service: Optional[HostDiagnosticsService] = None,
        analysis_service: Optional[TicketAnalysisService] = None,
        preflight_service: Optional[RoutingPreflightService] = None,
        catalog_service: Optional[ScenarioCatalogService] = None,
        auth_bootstrap: Optional[ServiceAuthBootstrap] = None,
    ) -> None:
        self.client = client or IntraServiceClient(
            base_url=settings.INTRASERVICE_URL,
            verify_ssl=settings.SSL_VERIFY,
        )
        self.policy_service = policy_service or AutopilotPolicyService()
        self.diagnostics_service = diagnostics_service or HostDiagnosticsService()
        self.preflight_service = preflight_service or RoutingPreflightService()
        self.analysis_service = analysis_service or TicketAnalysisService(
            client=self.client,
            policy_service=self.policy_service,
            preflight_service=self.preflight_service,
        )
        self.catalog_service = catalog_service or ScenarioCatalogService(
            policy_service=self.policy_service,
        )
        self.auth_bootstrap = auth_bootstrap or ServiceAuthBootstrap()
        self.fact_policy = FactReadinessPolicy()

    async def get_agent_plan(
        self,
        ticket_id: int,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
    ) -> AgentPlanDTO:
        """Strictly read-only plan query from cache or database."""
        return await self.analysis_service.get_plan_read_only(
            ticket_id=ticket_id,
            session=session,
            redis_client=redis_client,
        )

    async def analyze_ticket_plan(
        self,
        ticket_id: int,
        session: AsyncSession,
        force: bool = False,
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
    ) -> AgentPlanDTO:
        """Execute on-demand Evidence Routing Cascade analysis for a ticket."""
        auth_token = auth_b64
        if not auth_token:
            auth_creds = await self.auth_bootstrap.bootstrap_auth(
                client=self.client,
                redis_client=redis_client,
            )
            auth_token = auth_creds.auth_b64

        try:
            return await self.analysis_service.analyze_ticket(
                ticket_id=ticket_id,
                force=force,
                auth_b64=auth_token,
                session=session,
                redis_client=redis_client,
            )
        except IntraServiceNotFoundError as err:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Заявка #{ticket_id} не найдена в IntraService.",
            ) from err

    async def approve_plan(
        self,
        ticket_id: int,
        req: ApprovePlanRequest,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
        operator_username: str = "operator",
    ) -> ApprovePlanResponse:
        """Approve prepared agent plan and create dispatchable command record."""
        auth_token = auth_b64
        if not auth_token:
            auth_creds = await self.auth_bootstrap.bootstrap_auth(
                client=self.client,
                redis_client=redis_client,
            )
            auth_token = auth_creds.auth_b64

        # 1. Fetch fresh ticket state
        try:
            task: TaskDTO = await self.client.get_task(task_id=ticket_id, auth_b64=auth_token)
        except IntraServiceNotFoundError as err:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Заявка #{ticket_id} не найдена в IntraService.",
            ) from err

        # 2. OCC Status Check
        if task.status_id != req.expected_status_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Статус заявки изменился (текущий: {task.status_id} {task.status_name}, ожидаемый: {req.expected_status_id}). Выполните повторный анализ.",
                headers={"X-Error-Code": "status_mismatch"},
            )

        # 3. OCC Terminal Status Check
        if task.status_id in (3, 4, 30):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Заявка уже находится в конечном статусе '{task.status_name}'. Исполнение отменено.",
                headers={"X-Error-Code": "task_already_closed"},
            )

        # 4. Fetch Lifetime & Verify Snapshot Hash & Last Event ID
        lifetimes: List[TaskLifetimeEventDTO] = await self.client.get_task_lifetime(
            task_id=ticket_id, auth_b64=auth_token
        )
        current_snapshot = TicketSnapshotFactory.create(task, comments=lifetimes)

        if req.last_event_id is not None and current_snapshot.last_event_id != req.last_event_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"В заявке появились новые события (актуальный last_event_id: {current_snapshot.last_event_id}, ожидаемый: {req.last_event_id}). Выполните повторный анализ.",
                headers={"X-Error-Code": "event_id_mismatch"},
            )

        if current_snapshot.snapshot_hash != req.snapshot_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Контекст заявки изменился с момента построения плана. Выполните повторный анализ.",
                headers={"X-Error-Code": "snapshot_hash_mismatch"},
            )

        # 5. Fetch PreparedPlanRecord
        stmt = select(PreparedPlanRecord).where(PreparedPlanRecord.id == req.plan_id)
        plan_rec = (await session.execute(stmt)).scalar_one_or_none()
        if plan_rec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Подготовленный план {req.plan_id} не найден.",
            )

        if plan_rec.task_id != ticket_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="План привязан к другой заявке.",
            )

        if plan_rec.decision_id != req.decision_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Идентификатор решения в плане не совпадает с переданным.",
                headers={"X-Error-Code": "decision_id_mismatch"},
            )

        if plan_rec.snapshot_hash != req.snapshot_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Хеш снимка заявки в плане не совпадает с запросом.",
                headers={"X-Error-Code": "snapshot_hash_mismatch"},
            )

        if plan_rec.plan_hash != req.plan_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Хеш плана в базе данных не совпадает с запросом.",
                headers={"X-Error-Code": "plan_hash_mismatch"},
            )

        # 6. Check for conflicting terminal feedback
        stmt_prior_fb = (
            select(RoutingFeedbackRecord)
            .where(
                (RoutingFeedbackRecord.prepared_plan_id == plan_rec.id)
                | (
                    (RoutingFeedbackRecord.task_id == ticket_id)
                    & (RoutingFeedbackRecord.decision_id == plan_rec.decision_id)
                )
            )
            .order_by(desc(RoutingFeedbackRecord.created_at))
            .limit(1)
        )
        prior_fb = (await session.execute(stmt_prior_fb)).scalar_one_or_none()
        if prior_fb is not None and prior_fb.verdict in ("corrected", "rejected", "manual_takeover"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"План уже имеет терминальное решение '{prior_fb.verdict}'. Повторное утверждение отклонено.",
                headers={"X-Error-Code": "terminal_feedback_conflict"},
            )

        saved_plan_data = plan_rec.plan_json or {}
        saved_scenario = plan_rec.scenario_key or saved_plan_data.get("scenario_key")
        saved_params = saved_plan_data.get("proposed_params", {})
        saved_routing_state = saved_plan_data.get("routing_state") or plan_rec.state
        saved_is_executable = saved_plan_data.get("is_executable", False)

        try:
            canonical_recomputed_hash = compute_canonical_plan_hash_from_json(saved_plan_data)
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Сохраненный план не содержит полный канонический payload.",
                headers={"X-Error-Code": "plan_tampered"},
            ) from exc
        if canonical_recomputed_hash != plan_rec.plan_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Контрольная сумма сохраненного плана не прошла каноническую проверку.",
                headers={"X-Error-Code": "plan_tampered"},
            )

        execution_params = sanitize_secrets(saved_params)

        if saved_routing_state != "selected" or not saved_is_executable:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"План в состоянии '{saved_routing_state}' не может быть утвержден напрямую (is_executable={saved_is_executable}). Требуется корректировка параметров или ручная обработка.",
                headers={"X-Error-Code": "scenario_not_executable"},
            )

        # 7. Scenario Policy check
        policy: AutopilotPolicyDTO = await self.policy_service.get_policy(saved_scenario, session=session)
        if policy.mode == "DISABLED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Сценарий '{saved_scenario}' отключен политикой автопилота.",
                headers={"X-Error-Code": "scenario_disabled"},
            )

        # 8. Verify exact Preflight record
        if plan_rec.preflight_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Для исполнимого плана отсутствует связанная preflight-проверка.",
                headers={"X-Error-Code": "preflight_missing"},
            )

        stmt_pf = select(RoutingPreflightRecord).where(RoutingPreflightRecord.id == plan_rec.preflight_id)
        preflight_rec = (await session.execute(stmt_pf)).scalar_one_or_none()
        if preflight_rec is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Запись preflight-проверки для данного плана не найдена.",
                headers={"X-Error-Code": "preflight_missing"},
            )
        if preflight_rec.decision_id != req.decision_id or preflight_rec.snapshot_hash != req.snapshot_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Preflight-проверка привязана к другому решению или снимку.",
                headers={"X-Error-Code": "preflight_mismatch"},
            )
        if preflight_rec.scenario_key != saved_scenario:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Preflight-проверка выполнена для другого сценария.",
                headers={"X-Error-Code": "preflight_scenario_mismatch"},
            )
        expected_params_hash = compute_canonical_params_hash(saved_params)
        if preflight_rec.params_hash != expected_params_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Preflight-проверка выполнена для других параметров.",
                headers={"X-Error-Code": "preflight_params_mismatch"},
            )
        exp = preflight_rec.expires_at
        if exp is not None:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if datetime.now(UTC) > exp:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Preflight-проверка плана устарела (TTL 120с). Выполните повторный анализ заявки.",
                    headers={"X-Error-Code": "preflight_expired"},
                )
        if not is_executable_preflight_status(preflight_rec.status):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Статус preflight '{preflight_rec.status}' не разрешает создание команды. "
                    f"Допустимые статусы: {', '.join(sorted(EXECUTABLE_PREFLIGHT_STATUSES))}."
                ),
                headers={"X-Error-Code": "preflight_not_executable"},
            )

        # 8.1 Conflicting terminal feedback check
        stmt_prior_fb = (
            select(RoutingFeedbackRecord)
            .where(
                (RoutingFeedbackRecord.prepared_plan_id == plan_rec.id)
                | (
                    (RoutingFeedbackRecord.task_id == ticket_id)
                    & (RoutingFeedbackRecord.decision_id == plan_rec.decision_id)
                )
            )
            .order_by(desc(RoutingFeedbackRecord.created_at))
            .limit(1)
        )
        prior_fb = (await session.execute(stmt_prior_fb)).scalar_one_or_none()
        if prior_fb is not None and prior_fb.verdict in ("corrected", "rejected", "manual_takeover"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"План уже имеет терминальное решение '{prior_fb.verdict}'. Утверждение отклонено.",
                headers={"X-Error-Code": "terminal_feedback_conflict"},
            )

        # 9. Idempotency Key check & retry support
        base_idempotency_key = f"approved_{ticket_id}_{plan_rec.plan_hash}"
        stmt_cmd = select(CommandRecord).where(
            (CommandRecord.idempotency_key.startswith(base_idempotency_key))
            | ((CommandRecord.task_id == ticket_id) & (CommandRecord.plan_hash == plan_rec.plan_hash))
        ).order_by(desc(CommandRecord.created_at)).limit(1)
        existing_cmd = (await session.execute(stmt_cmd)).scalar_one_or_none()
        if existing_cmd is not None and existing_cmd.status in ("pending", "running", "succeeded"):
            logger.info("Reusing existing CommandRecord %s for ticket #%d", existing_cmd.id, ticket_id)
            return ApprovePlanResponse(
                status="approved",
                command_id=str(existing_cmd.id),
                ticket_id=ticket_id,
                action=saved_scenario,
                plan_hash=plan_rec.plan_hash,
                is_duplicate=True,
            )

        # If previous command was failed/needs_review/canceled, assign a unique retry key
        if existing_cmd is not None:
            idempotency_key = f"{base_idempotency_key}_retry_{uuid.uuid4().hex[:8]}"
        else:
            idempotency_key = base_idempotency_key

        # 10. Query decision record to get versions and verifier info
        stmt_dec = select(RoutingDecisionRecord).where(RoutingDecisionRecord.id == plan_rec.decision_id)
        dec_rec = (await session.execute(stmt_dec)).scalar_one_or_none()
        router_ver = dec_rec.router_version if dec_rec else "2.0.0"
        prompt_ver = dec_rec.prompt_version if dec_rec else None
        verifier_used = bool(dec_rec.verifier_result_json) if dec_rec else False

        # 11. Transactional persistence: RoutingFeedbackRecord + CommandRecord (Single Source of Truth)
        cmd_id = uuid.uuid4()
        feedback = RoutingFeedbackRecord(
            decision_id=plan_rec.decision_id,
            prepared_plan_id=plan_rec.id,
            command_id=cmd_id,
            task_id=ticket_id,
            snapshot_hash=req.snapshot_hash,
            operator_username=operator_username,
            verdict="approved",
            original_scenario=saved_scenario,
            corrected_scenario=saved_scenario,
            original_params=execution_params,
            corrected_params=execution_params,
            reason_tag="approved",
            operator_notes=None,
            notes=None,
            router_version=router_ver,
            prompt_version=prompt_ver,
            verifier_used=verifier_used,
            source="runtime",
        )
        session.add(feedback)

        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key=idempotency_key,
            action=saved_scenario,
            executor="api",
            target_json={"ticket_id": ticket_id, "pc_name": task.entities.pc_name},
            params_json={
                **execution_params,
                "expected_status_id": req.expected_status_id,
                "approved_scenario": saved_scenario,
            },
            status="pending",
            initiator=f"supervisor:{operator_username}",
            task_id=ticket_id,
            decision_id=plan_rec.decision_id,
            plan_id=plan_rec.id,
            preflight_id=preflight_rec.id,
            plan_hash=plan_rec.plan_hash,
        )
        try:
            session.add(cmd)
            await session.commit()
            await session.refresh(cmd)
        except IntegrityError:
            await session.rollback()
            # Double check if prior feedback or command exists
            existing_fb = (await session.execute(stmt_prior_fb)).scalar_one_or_none()
            if existing_fb is not None and existing_fb.verdict == "approved":
                existing_cmd = (await session.execute(stmt_cmd)).scalar_one_or_none()
                if existing_cmd is not None:
                    return ApprovePlanResponse(
                        status="approved",
                        command_id=str(existing_cmd.id),
                        ticket_id=ticket_id,
                        action=saved_scenario,
                        plan_hash=plan_rec.plan_hash,
                        is_duplicate=True,
                    )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Конфликт терминальных решений для данного плана.",
                headers={"X-Error-Code": "terminal_feedback_conflict"},
            ) from None

        if redis_client is not None:
            try:
                await redis_client.delete(f"cache:autopilot:plan:{ticket_id}")
            except Exception:
                pass

        # 12. Dispatch Taskiq task
        try:
            await dispatch_command(cmd.id)
        except Exception as exc:
            logger.warning("Failed to dispatch Taskiq task for approved command %s: %s", cmd.id, exc)

        emit_routing_event(
            "plan_approved",
            task_id=ticket_id,
            decision_id=plan_rec.decision_id,
            plan_id=plan_rec.id,
            command_id=cmd.id,
            scenario_key=saved_scenario,
            routing_state="approved",
        )

        logger.info(
            "Operator %s approved plan for ticket #%d (command: %s, plan_hash: %s)",
            operator_username,
            ticket_id,
            cmd.id,
            plan_rec.plan_hash,
        )
        return ApprovePlanResponse(
            status="approved",
            command_id=str(cmd.id),
            ticket_id=ticket_id,
            action=saved_scenario,
            plan_hash=plan_rec.plan_hash,
            is_duplicate=False,
        )

    async def correct_plan(
        self,
        ticket_id: int,
        req: CorrectPlanRequest,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
        operator_username: str = "operator",
    ) -> CorrectPlanResponse:
        """Correct agent plan parameters and scenario with verified preflight gating."""
        auth_token = auth_b64
        if not auth_token:
            auth_creds = await self.auth_bootstrap.bootstrap_auth(
                client=self.client,
                redis_client=redis_client,
            )
            auth_token = auth_creds.auth_b64

        # 1. Fetch fresh ticket state
        try:
            task: TaskDTO = await self.client.get_task(task_id=ticket_id, auth_b64=auth_token)
        except IntraServiceNotFoundError as err:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Заявка #{ticket_id} не найдена в IntraService.",
            ) from err

        # 2. OCC Status Check
        if task.status_id != req.expected_status_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Статус заявки изменился (текущий: {task.status_id} {task.status_name}, ожидаемый: {req.expected_status_id}). Выполните повторный анализ.",
                headers={"X-Error-Code": "status_mismatch"},
            )

        if task.status_id in (3, 4, 30):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Заявка уже находится в конечном статусе '{task.status_name}'. Исполнение отменено.",
                headers={"X-Error-Code": "task_already_closed"},
            )

        # 3. Lifetime & Snapshot OCC check
        lifetimes: List[TaskLifetimeEventDTO] = await self.client.get_task_lifetime(
            task_id=ticket_id, auth_b64=auth_token
        )
        current_snapshot = TicketSnapshotFactory.create(task, comments=lifetimes)

        if req.last_event_id is not None and current_snapshot.last_event_id != req.last_event_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"В заявке появились новые события (актуальный last_event_id: {current_snapshot.last_event_id}, ожидаемый: {req.last_event_id}). Выполните повторный анализ.",
                headers={"X-Error-Code": "event_id_mismatch"},
            )

        if current_snapshot.snapshot_hash != req.snapshot_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Контекст заявки изменился с момента построения плана. Выполните повторный анализ.",
                headers={"X-Error-Code": "snapshot_hash_mismatch"},
            )

        # 4. Fetch PreparedPlanRecord
        stmt = select(PreparedPlanRecord).where(PreparedPlanRecord.id == req.plan_id)
        plan_rec = (await session.execute(stmt)).scalar_one_or_none()
        if plan_rec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Подготовленный план {req.plan_id} не найден.",
            )

        if plan_rec.task_id != ticket_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="План привязан к другой заявке.",
            )

        if plan_rec.decision_id != req.decision_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Идентификатор решения в плане не совпадает с переданным.",
                headers={"X-Error-Code": "decision_id_mismatch"},
            )

        if plan_rec.snapshot_hash != req.snapshot_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Хеш снимка заявки в плане не совпадает с запросом.",
                headers={"X-Error-Code": "snapshot_hash_mismatch"},
            )

        if plan_rec.plan_hash != req.plan_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Хеш плана в базе данных не совпадает с запросом.",
                headers={"X-Error-Code": "plan_hash_mismatch"},
            )

        saved_plan_data = plan_rec.plan_json or {}
        try:
            canonical_recomputed_hash = compute_canonical_plan_hash_from_json(saved_plan_data)
        except (TypeError, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Сохраненный план не содержит полный канонический payload.",
                headers={"X-Error-Code": "plan_tampered"},
            ) from exc
        if canonical_recomputed_hash != plan_rec.plan_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Контрольная сумма сохраненного плана не прошла каноническую проверку.",
                headers={"X-Error-Code": "plan_tampered"},
            )

        # 5. Check conflicting terminal feedback
        stmt_prior_fb = (
            select(RoutingFeedbackRecord)
            .where(
                (RoutingFeedbackRecord.prepared_plan_id == plan_rec.id)
                | (
                    (RoutingFeedbackRecord.task_id == ticket_id)
                    & (RoutingFeedbackRecord.decision_id == plan_rec.decision_id)
                )
            )
            .order_by(desc(RoutingFeedbackRecord.created_at))
            .limit(1)
        )
        prior_fb = (await session.execute(stmt_prior_fb)).scalar_one_or_none()
        if prior_fb is not None and prior_fb.verdict in ("approved", "rejected", "manual_takeover"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"План уже имеет терминальное решение '{prior_fb.verdict}'. Корректировка отклонена.",
                headers={"X-Error-Code": "terminal_feedback_conflict"},
            )

        # 6. Validate corrected scenario against catalog and policy
        catalog = await self.catalog_service.get_catalog(session=session)
        catalog_items = {s.scenario_key: s for s in catalog}
        if req.corrected_scenario not in catalog_items:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Неизвестный сценарий '{req.corrected_scenario}'. Выберите сценарий из каталога.",
            )

        cat_item = catalog_items[req.corrected_scenario]
        if not cat_item.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Сценарий '{req.corrected_scenario}' отключен: {cat_item.disabled_reason}",
                headers={"X-Error-Code": "scenario_disabled"},
            )

        sanitized_corrected_params = sanitize_secrets(req.corrected_params)
        sanitized_operator_notes = sanitize_value(req.operator_notes) if req.operator_notes else None

        # 7. Execute preflight validation for corrected scenario & params
        preflight_res = await self.preflight_service.execute_preflight(
            decision_id=plan_rec.decision_id,
            snapshot=current_snapshot,
            scenario_key=req.corrected_scenario,
            params=sanitized_corrected_params,
            session=session,
        )

        now_utc = datetime.now(timezone.utc)
        if (
            not is_executable_preflight_status(preflight_res.status)
            or preflight_res.id is None
            or preflight_res.is_expired
            or (preflight_res.expires_at is not None and preflight_res.expires_at <= now_utc)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Preflight-проверка для сценария '{req.corrected_scenario}' не прошла или устарела ({preflight_res.status}): {preflight_res.error_message or 'компоненты недоступны'}.",
                headers={"X-Error-Code": "preflight_failed"},
            )

        # 8. Compute new canonical plan hash
        meta = _SCENARIO_META.get(req.corrected_scenario, {})
        new_comment = req.corrected_comment or meta.get("comment", "")
        new_status_id = meta.get("target_status_id", 3)

        new_plan_hash = compute_canonical_plan_hash(
            task_id=ticket_id,
            decision_id=plan_rec.decision_id,
            snapshot_hash=req.snapshot_hash,
            scenario_key=req.corrected_scenario,
            proposed_params=sanitized_corrected_params,
            suggested_comment=new_comment,
            target_status_id=new_status_id,
        )

        # 9. Idempotency check for repeat identical correction
        idempotency_key = f"corrected_{ticket_id}_{new_plan_hash}"
        stmt_cmd = select(CommandRecord).where(
            (CommandRecord.idempotency_key == idempotency_key)
            | ((CommandRecord.task_id == ticket_id) & (CommandRecord.plan_hash == new_plan_hash))
        )
        existing_cmd = (await session.execute(stmt_cmd)).scalar_one_or_none()
        if existing_cmd is not None and existing_cmd.status in ("pending", "running", "succeeded"):
            logger.info("Reusing existing CommandRecord %s for corrected ticket #%d", existing_cmd.id, ticket_id)
            return CorrectPlanResponse(
                status="corrected",
                command_id=str(existing_cmd.id),
                ticket_id=ticket_id,
                action=req.corrected_scenario,
                plan_id=str(plan_rec.id),
                plan_hash=new_plan_hash,
                is_duplicate=True,
            )

        # 10. Persist new Preflight record, new PreparedPlanRecord & RoutingFeedbackRecord atomically
        preflight_record = RoutingPreflightRecord(
            id=preflight_res.id,
            decision_id=plan_rec.decision_id,
            task_id=ticket_id,
            snapshot_hash=req.snapshot_hash,
            scenario_key=req.corrected_scenario,
            params_hash=preflight_res.params_hash,
            status=preflight_res.status,
            checks_json=[c.model_dump(mode="json") for c in preflight_res.checks],
            details_json=preflight_res.details,
            error_message=preflight_res.error_message,
            expires_at=preflight_res.expires_at or (datetime.now(UTC) + timedelta(seconds=120)),
        )
        session.add(preflight_record)
        await session.flush()

        new_plan_id = uuid.uuid4()
        new_plan_dto = AgentPlanDTO(
            task_id=ticket_id,
            scenario_key=req.corrected_scenario,
            scenario_name=cat_item.name,
            description=cat_item.description,
            analysis_state="ready",
            routing_state="selected",
            approval_state="approved",
            can_approve=True,
            can_correct=True,
            can_reject=True,
            blocking_reason_codes=[],
            is_stale=False,
            freshness_expires_at=preflight_res.expires_at,
            has_terminal_feedback=True,
            decision_id=plan_rec.decision_id,
            snapshot_hash=req.snapshot_hash,
            plan_id=new_plan_id,
            plan_hash=new_plan_hash,
            decision_reason_codes=["operator_corrected"],
            degraded_components={},
            missing_facts=[],
            preflight=preflight_res,
            is_executable=True,
            evidence_summaries=[],
            extracted_entities=dict(current_snapshot.entities),
            candidate_hosts=[sanitized_corrected_params.get("pc_name", "")] if sanitized_corrected_params.get("pc_name") else [],
            proposed_action=req.corrected_scenario,
            proposed_params=sanitized_corrected_params,
            suggested_comment=new_comment,
            target_status_id=new_status_id,
            last_event_id=current_snapshot.last_event_id,
            is_circuit_broken=False,
            mode=cat_item.policy_mode,
        )

        new_plan_record = PreparedPlanRecord(
            id=new_plan_id,
            task_id=ticket_id,
            decision_id=plan_rec.decision_id,
            snapshot_hash=req.snapshot_hash,
            plan_hash=new_plan_hash,
            scenario_key=req.corrected_scenario,
            preflight_id=preflight_record.id,
            plan_json=new_plan_dto.model_dump(mode="json"),
            state="selected",
        )
        session.add(new_plan_record)
        await session.flush()

        stmt_dec = select(RoutingDecisionRecord).where(RoutingDecisionRecord.id == plan_rec.decision_id)
        dec_rec = (await session.execute(stmt_dec)).scalar_one_or_none()
        router_ver = dec_rec.router_version if dec_rec else "2.0.0"
        prompt_ver = dec_rec.prompt_version if dec_rec else None
        verifier_used = bool(dec_rec.verifier_result_json) if dec_rec else False

        saved_plan_data = plan_rec.plan_json or {}
        orig_scenario = plan_rec.scenario_key or saved_plan_data.get("scenario_key", "unknown")
        orig_params = sanitize_secrets(saved_plan_data.get("proposed_params", {}))

        cmd_id = uuid.uuid4()
        feedback = RoutingFeedbackRecord(
            decision_id=plan_rec.decision_id,
            # The feedback terminates the operator's reviewed plan. The command
            # itself remains bound to the newly materialized corrected plan.
            prepared_plan_id=plan_rec.id,
            command_id=cmd_id,
            task_id=ticket_id,
            snapshot_hash=req.snapshot_hash,
            operator_username=operator_username,
            verdict="corrected",
            original_scenario=orig_scenario,
            corrected_scenario=req.corrected_scenario,
            original_params=orig_params,
            corrected_params=sanitized_corrected_params,
            reason_tag=req.correction_tag,
            operator_notes=sanitized_operator_notes,
            notes=sanitized_operator_notes,
            router_version=router_ver,
            prompt_version=prompt_ver,
            verifier_used=verifier_used,
            source="runtime",
        )
        session.add(feedback)

        cmd = CommandRecord(
            id=cmd_id,
            idempotency_key=idempotency_key,
            action=req.corrected_scenario,
            executor="api",
            target_json={"ticket_id": ticket_id, "pc_name": sanitized_corrected_params.get("pc_name")},
            params_json={
                **sanitized_corrected_params,
                "expected_status_id": req.expected_status_id,
                "approved_scenario": req.corrected_scenario,
                "is_corrected": True,
                "correction_tag": req.correction_tag,
            },
            status="pending",
            initiator=f"supervisor:{operator_username}",
            task_id=ticket_id,
            decision_id=plan_rec.decision_id,
            plan_id=new_plan_id,
            preflight_id=preflight_record.id,
            plan_hash=new_plan_hash,
        )
        try:
            session.add(cmd)
            await session.commit()
            await session.refresh(cmd)
        except IntegrityError:
            await session.rollback()
            existing_cmd = (await session.execute(stmt_cmd)).scalar_one_or_none()
            if existing_cmd is not None and existing_cmd.status in ("pending", "running", "succeeded"):
                return CorrectPlanResponse(
                    status="corrected",
                    command_id=str(existing_cmd.id),
                    ticket_id=ticket_id,
                    action=req.corrected_scenario,
                    plan_id=str(plan_rec.id),
                    plan_hash=new_plan_hash,
                    is_duplicate=True,
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Конфликт параметров корректировки плана.",
                headers={"X-Error-Code": "terminal_feedback_conflict"},
            ) from None

        if redis_client is not None:
            try:
                await redis_client.delete(f"cache:autopilot:plan:{ticket_id}")
            except Exception:
                pass

        try:
            await dispatch_command(cmd.id)
        except Exception as exc:
            logger.warning("Failed to dispatch Taskiq task for corrected command %s: %s", cmd.id, exc)

        emit_routing_event(
            "plan_corrected",
            task_id=ticket_id,
            decision_id=plan_rec.decision_id,
            plan_id=new_plan_id,
            command_id=cmd.id,
            scenario_key=req.corrected_scenario,
            routing_state="corrected",
        )

        logger.info(
            "Operator %s corrected plan for ticket #%d (scenario: %s -> %s, command: %s, plan_hash: %s)",
            operator_username,
            ticket_id,
            orig_scenario,
            req.corrected_scenario,
            cmd.id,
            new_plan_hash,
        )
        return CorrectPlanResponse(
            status="corrected",
            command_id=str(cmd.id),
            ticket_id=ticket_id,
            action=req.corrected_scenario,
            plan_id=str(new_plan_id),
            plan_hash=new_plan_hash,
            is_duplicate=False,
        )

    async def reject_plan(
        self,
        ticket_id: int,
        req: RejectPlanRequest,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
        operator_username: str = "operator",
    ) -> RejectPlanResponse:
        """Reject agent plan proposal without creating any execution command."""
        stmt = select(PreparedPlanRecord).where(PreparedPlanRecord.id == req.plan_id)
        plan_rec = (await session.execute(stmt)).scalar_one_or_none()
        if plan_rec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"План {req.plan_id} не найден.",
            )

        if plan_rec.task_id != ticket_id or plan_rec.plan_hash != req.plan_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Хеш или ID плана не совпадает.",
                headers={"X-Error-Code": "plan_mismatch"},
            )

        if req.decision_id and plan_rec.decision_id and str(req.decision_id) != str(plan_rec.decision_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Идентификатор решения не совпадает с планом.",
                headers={"X-Error-Code": "decision_id_mismatch"},
            )

        if req.snapshot_hash and plan_rec.snapshot_hash and req.snapshot_hash != plan_rec.snapshot_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Хеш снимка заявки не совпадает с планом.",
                headers={"X-Error-Code": "snapshot_hash_mismatch"},
            )

        # Check existing terminal feedback
        stmt_fb = (
            select(RoutingFeedbackRecord)
            .where(
                (RoutingFeedbackRecord.prepared_plan_id == req.plan_id)
                | (
                    (RoutingFeedbackRecord.task_id == ticket_id)
                    & (RoutingFeedbackRecord.decision_id == plan_rec.decision_id)
                )
            )
            .order_by(desc(RoutingFeedbackRecord.created_at))
            .limit(1)
        )
        existing_fb = (await session.execute(stmt_fb)).scalar_one_or_none()
        if existing_fb is not None:
            if existing_fb.verdict == "rejected":
                return RejectPlanResponse(
                    status="rejected",
                    feedback_id=str(existing_fb.id),
                    ticket_id=ticket_id,
                    is_duplicate=True,
                )
            elif existing_fb.verdict in ("approved", "corrected", "manual_takeover"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"План уже имеет терминальное решение '{existing_fb.verdict}'. Отклонение невозможно.",
                    headers={"X-Error-Code": "terminal_feedback_conflict"},
                )

        stmt_dec = select(RoutingDecisionRecord).where(RoutingDecisionRecord.id == plan_rec.decision_id)
        dec_rec = (await session.execute(stmt_dec)).scalar_one_or_none()
        router_ver = dec_rec.router_version if dec_rec else "2.0.0"
        prompt_ver = dec_rec.prompt_version if dec_rec else None
        verifier_used = bool(dec_rec.verifier_result_json) if dec_rec else False

        saved_plan_data = plan_rec.plan_json or {}
        orig_scenario = plan_rec.scenario_key or saved_plan_data.get("scenario_key", "unknown")
        orig_params = sanitize_secrets(saved_plan_data.get("proposed_params", {}))

        feedback = RoutingFeedbackRecord(
            decision_id=plan_rec.decision_id,
            prepared_plan_id=plan_rec.id,
            command_id=None,
            task_id=ticket_id,
            snapshot_hash=plan_rec.snapshot_hash,
            operator_username=operator_username,
            verdict="rejected",
            original_scenario=orig_scenario,
            corrected_scenario=None,
            original_params=orig_params,
            corrected_params={},
            reason_tag=req.reason_tag,
            operator_notes=sanitize_value(req.operator_notes) if req.operator_notes else None,
            notes=sanitize_value(req.operator_notes) if req.operator_notes else None,
            router_version=router_ver,
            prompt_version=prompt_ver,
            verifier_used=verifier_used,
            source="runtime",
        )
        try:
            session.add(feedback)
            await session.commit()
            await session.refresh(feedback)
        except IntegrityError:
            await session.rollback()
            existing_fb = (await session.execute(stmt_fb)).scalar_one_or_none()
            if existing_fb is not None and existing_fb.verdict == "rejected":
                return RejectPlanResponse(
                    status="rejected",
                    feedback_id=str(existing_fb.id),
                    ticket_id=ticket_id,
                    is_duplicate=True,
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Конфликт терминальных решений для данного плана.",
                headers={"X-Error-Code": "terminal_feedback_conflict"},
            ) from None

        if redis_client is not None:
            try:
                await redis_client.delete(f"cache:autopilot:plan:{ticket_id}")
            except Exception:
                pass

        emit_routing_event(
            "plan_rejected",
            task_id=ticket_id,
            decision_id=plan_rec.decision_id,
            plan_id=plan_rec.id,
            scenario_key=orig_scenario,
            routing_state="rejected",
        )

        logger.info("Operator %s rejected plan for ticket #%d", operator_username, ticket_id)
        return RejectPlanResponse(
            status="rejected",
            feedback_id=str(feedback.id),
            ticket_id=ticket_id,
            is_duplicate=False,
        )

    async def manual_takeover_plan(
        self,
        ticket_id: int,
        req: ManualTakeoverRequest,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
        operator_username: str = "operator",
    ) -> ManualTakeoverResponse:
        """Assign ticket to human engineer without executing autonomous scenarios."""
        stmt = select(PreparedPlanRecord).where(PreparedPlanRecord.id == req.plan_id)
        plan_rec = (await session.execute(stmt)).scalar_one_or_none()
        if plan_rec is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"План {req.plan_id} не найден.",
            )

        if plan_rec.task_id != ticket_id or plan_rec.plan_hash != req.plan_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Хеш или ID плана не совпадает.",
                headers={"X-Error-Code": "plan_mismatch"},
            )

        if req.decision_id and plan_rec.decision_id and str(req.decision_id) != str(plan_rec.decision_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Идентификатор решения не совпадает с планом.",
                headers={"X-Error-Code": "decision_id_mismatch"},
            )

        if req.snapshot_hash and plan_rec.snapshot_hash and req.snapshot_hash != plan_rec.snapshot_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Хеш снимка заявки не совпадает с планом.",
                headers={"X-Error-Code": "snapshot_hash_mismatch"},
            )

        stmt_fb = (
            select(RoutingFeedbackRecord)
            .where(
                (RoutingFeedbackRecord.prepared_plan_id == req.plan_id)
                | (
                    (RoutingFeedbackRecord.task_id == ticket_id)
                    & (RoutingFeedbackRecord.decision_id == plan_rec.decision_id)
                )
            )
            .order_by(desc(RoutingFeedbackRecord.created_at))
            .limit(1)
        )
        existing_fb = (await session.execute(stmt_fb)).scalar_one_or_none()
        if existing_fb is not None:
            if existing_fb.verdict == "manual_takeover":
                return ManualTakeoverResponse(
                    status="manual_takeover",
                    feedback_id=str(existing_fb.id),
                    ticket_id=ticket_id,
                    is_duplicate=True,
                )
            elif existing_fb.verdict in ("approved", "corrected", "rejected"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"План уже имеет терминальное решение '{existing_fb.verdict}'. Передача инженеру отклонена.",
                    headers={"X-Error-Code": "terminal_feedback_conflict"},
                )

        stmt_dec = select(RoutingDecisionRecord).where(RoutingDecisionRecord.id == plan_rec.decision_id)
        dec_rec = (await session.execute(stmt_dec)).scalar_one_or_none()
        router_ver = dec_rec.router_version if dec_rec else "2.0.0"
        prompt_ver = dec_rec.prompt_version if dec_rec else None
        verifier_used = bool(dec_rec.verifier_result_json) if dec_rec else False

        saved_plan_data = plan_rec.plan_json or {}
        orig_scenario = plan_rec.scenario_key or saved_plan_data.get("scenario_key", "unknown")
        orig_params = sanitize_secrets(saved_plan_data.get("proposed_params", {}))

        feedback = RoutingFeedbackRecord(
            decision_id=plan_rec.decision_id,
            prepared_plan_id=plan_rec.id,
            command_id=None,
            task_id=ticket_id,
            snapshot_hash=plan_rec.snapshot_hash,
            operator_username=operator_username,
            verdict="manual_takeover",
            original_scenario=orig_scenario,
            corrected_scenario=None,
            original_params=orig_params,
            corrected_params={},
            reason_tag=req.reason_tag,
            operator_notes=sanitize_value(req.operator_notes) if req.operator_notes else None,
            notes=sanitize_value(req.operator_notes) if req.operator_notes else None,
            router_version=router_ver,
            prompt_version=prompt_ver,
            verifier_used=verifier_used,
            source="runtime",
        )
        try:
            session.add(feedback)
            await session.commit()
            await session.refresh(feedback)
        except IntegrityError:
            await session.rollback()
            existing_fb = (await session.execute(stmt_fb)).scalar_one_or_none()
            if existing_fb is not None and existing_fb.verdict == "manual_takeover":
                return ManualTakeoverResponse(
                    status="manual_takeover",
                    feedback_id=str(existing_fb.id),
                    ticket_id=ticket_id,
                    is_duplicate=True,
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Конфликт терминальных решений для данного плана.",
                headers={"X-Error-Code": "terminal_feedback_conflict"},
            ) from None

        # 1. Set abort flag in Redis and clean plan cache
        if redis_client is not None:
            try:
                await redis_client.set(f"autopilot:abort:{ticket_id}", "1", ex=86400 * 30)
                await redis_client.delete(f"cache:autopilot:plan:{ticket_id}")
            except Exception as exc:
                logger.warning("Failed setting abort flag in Redis for ticket #%d: %s", ticket_id, exc)

        # 2. Add private technical note and transfer ticket in IntraService
        external_update_succeeded = False
        external_update_warning: Optional[str] = None
        try:
            auth_token = None
            if self.auth_bootstrap:
                auth_creds = await self.auth_bootstrap.bootstrap_auth(client=self.client, redis_client=redis_client)
                auth_token = auth_creds.auth_b64
            note_text = f"Заявка передана на ручную обработку инженеру оператором {operator_username}. Автономные сценарии автопилота остановлены. Причина: {req.reason_tag}."
            if req.operator_notes:
                note_text += f" Заметки: {sanitize_value(req.operator_notes)}"
            await self.client.update_task(
                task_id=ticket_id,
                status_id=2,  # В работе
                comment=note_text,
                is_private=True,
                auth_b64=auth_token,
            )
            external_update_succeeded = True
        except Exception as exc:
            logger.warning("Failed updating manual takeover state for ticket #%d in IntraService: %s", ticket_id, exc)
            external_update_warning = (
                "Автоматизация остановлена в журнале IntraLink, но обновление статуса и служебной "
                "заметки в IntraService не подтверждено. Проверьте заявку вручную."
            )

        emit_routing_event(
            "manual_takeover_initiated",
            task_id=ticket_id,
            decision_id=plan_rec.decision_id,
            plan_id=plan_rec.id,
            scenario_key=orig_scenario,
            routing_state="manual_takeover",
        )

        logger.info("Operator %s took over ticket #%d manually", operator_username, ticket_id)
        return ManualTakeoverResponse(
            status="manual_takeover",
            feedback_id=str(feedback.id),
            ticket_id=ticket_id,
            is_duplicate=False,
            external_update_succeeded=external_update_succeeded,
            warning=external_update_warning,
        )

    async def get_feedback_list(
        self,
        session: AsyncSession,
        task_id: Optional[int] = None,
        verdict: Optional[str] = None,
        scenario_key: Optional[str] = None,
        reason_tag: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> FeedbackListResponse:
        """Retrieve paginated historical routing feedback records."""
        stmt = select(RoutingFeedbackRecord)
        if task_id is not None:
            stmt = stmt.where(RoutingFeedbackRecord.task_id == task_id)
        if verdict is not None:
            stmt = stmt.where(RoutingFeedbackRecord.verdict == verdict)
        if scenario_key is not None:
            stmt = stmt.where(
                (RoutingFeedbackRecord.original_scenario == scenario_key)
                | (RoutingFeedbackRecord.corrected_scenario == scenario_key)
            )
        if reason_tag is not None:
            stmt = stmt.where(RoutingFeedbackRecord.reason_tag == reason_tag)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(desc(RoutingFeedbackRecord.created_at)).offset(offset).limit(limit)
        res = await session.execute(stmt)
        records = res.scalars().all()

        dtos = [
            RoutingFeedbackDTO(
                id=r.id,
                decision_id=r.decision_id,
                prepared_plan_id=r.prepared_plan_id,
                command_id=r.command_id,
                task_id=r.task_id,
                snapshot_hash=r.snapshot_hash,
                operator_username=r.operator_username,
                verdict=r.verdict,
                original_scenario=r.original_scenario,
                corrected_scenario=r.corrected_scenario,
                original_params=r.original_params,
                corrected_params=r.corrected_params,
                reason_tag=r.reason_tag,
                operator_notes=r.operator_notes,
                router_version=r.router_version,
                prompt_version=r.prompt_version,
                verifier_used=r.verifier_used,
                source=r.source,
                created_at=r.created_at,
            )
            for r in records
        ]

        return FeedbackListResponse(feedback=dtos, total=total)

    async def export_feedback(
        self,
        session: AsyncSession,
        scenario_key: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_samples: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Export sanitized, versioned calibration dataset with full decision/plan provenance."""
        stmt = (
            select(RoutingFeedbackRecord)
            .where(
                RoutingFeedbackRecord.source.in_(["runtime", "legacy_verified"]),
                RoutingFeedbackRecord.decision_id.isnot(None),
            )
        )
        if scenario_key is not None:
            stmt = stmt.where(
                (RoutingFeedbackRecord.original_scenario == scenario_key)
                | (RoutingFeedbackRecord.corrected_scenario == scenario_key)
            )
        if start_date is not None:
            stmt = stmt.where(RoutingFeedbackRecord.created_at >= start_date)
        if end_date is not None:
            stmt = stmt.where(RoutingFeedbackRecord.created_at <= end_date)

        stmt = stmt.order_by(RoutingFeedbackRecord.created_at.asc())
        res = await session.execute(stmt)
        records = res.scalars().all()

        dataset: List[Dict[str, Any]] = []
        for r in records:
            dataset.append({
                "schema_version": "2.0.0",
                "feedback_id": str(r.id),
                "task_id": r.task_id,
                "decision_id": str(r.decision_id) if r.decision_id else None,
                "prepared_plan_id": str(r.prepared_plan_id) if r.prepared_plan_id else None,
                "command_id": str(r.command_id) if r.command_id else None,
                "snapshot_hash": r.snapshot_hash,
                "verdict": r.verdict,
                "original_scenario": r.original_scenario,
                "corrected_scenario": r.corrected_scenario,
                "original_params": sanitize_secrets(r.original_params),
                "corrected_params": sanitize_secrets(r.corrected_params),
                "reason_tag": r.reason_tag,
                "operator_username": r.operator_username,
                "router_version": r.router_version,
                "prompt_version": r.prompt_version,
                "verifier_used": r.verifier_used,
                "source": r.source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })

        if min_samples is not None and len(dataset) < min_samples:
            return []

        return dataset

    async def get_quality_metrics(
        self,
        session: AsyncSession,
        scenario_key: Optional[str] = None,
        router_version: Optional[str] = None,
        prompt_version: Optional[str] = None,
        routing_state: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> RoutingQualityMetricsDTO:
        """Compute aggregated calibration and routing performance metrics without PII or synthetic constants."""
        stmt_dec = select(RoutingDecisionRecord)
        if scenario_key:
            stmt_dec = stmt_dec.where(RoutingDecisionRecord.selected_scenario == scenario_key)
        if router_version:
            stmt_dec = stmt_dec.where(RoutingDecisionRecord.router_version == router_version)
        if prompt_version:
            stmt_dec = stmt_dec.where(RoutingDecisionRecord.prompt_version == prompt_version)
        if routing_state:
            stmt_dec = stmt_dec.where(RoutingDecisionRecord.state == routing_state)
        if start_date:
            stmt_dec = stmt_dec.where(RoutingDecisionRecord.created_at >= start_date)
        if end_date:
            stmt_dec = stmt_dec.where(RoutingDecisionRecord.created_at <= end_date)

        decisions = (await session.execute(stmt_dec)).scalars().all()
        total_decisions = len(decisions)

        if total_decisions == 0:
            return RoutingQualityMetricsDTO(
                total_decisions=0,
                by_routing_state={},
                approve_rate=0.0,
                correction_rate=0.0,
                reject_takeover_rate=0.0,
                by_scenario={},
                scenario_transitions=[],
                llm_verifier_call_rate=0.0,
                verifier_agreement_rate=None,
                degraded_provider_rate=0.0,
                missing_facts_rate=0.0,
                preflight_failure_rate=0.0,
                command_success_rate=0.0,
                command_failure_rate=0.0,
                p50_latency_ms=None,
                p95_latency_ms=None,
            )

        decision_ids = [d.id for d in decisions]
        by_routing_state: Dict[str, int] = {}
        by_scenario: Dict[str, int] = {}
        llm_calls = 0
        missing_facts_count = 0
        degraded_count = 0
        latencies: List[float] = []

        for d in decisions:
            by_routing_state[d.state] = by_routing_state.get(d.state, 0) + 1
            if d.selected_scenario:
                by_scenario[d.selected_scenario] = by_scenario.get(d.selected_scenario, 0) + 1
            if d.verifier_result_json:
                llm_calls += 1
            if d.missing_facts_json:
                missing_facts_count += 1
            if d.degraded_components_json:
                degraded_count += 1

            if d.verifier_trace_json and isinstance(d.verifier_trace_json, dict) and "duration_ms" in d.verifier_trace_json:
                try:
                    latencies.append(float(d.verifier_trace_json["duration_ms"]))
                except (ValueError, TypeError):
                    pass
            elif d.snapshot_json and isinstance(d.snapshot_json, dict) and "_metrics" in d.snapshot_json:
                m = d.snapshot_json.get("_metrics")
                if isinstance(m, dict) and "duration_ms" in m:
                    try:
                        latencies.append(float(m["duration_ms"]))
                    except (ValueError, TypeError):
                        pass

        # Feedback aggregates strictly joined to filtered decision population
        stmt_fb = (
            select(RoutingFeedbackRecord)
            .where(
                RoutingFeedbackRecord.decision_id.in_(decision_ids),
                RoutingFeedbackRecord.source.in_(["runtime", "legacy_verified"]),
            )
        )
        feedbacks = (await session.execute(stmt_fb)).scalars().all()
        total_fb = len(feedbacks)

        approved = sum(1 for f in feedbacks if f.verdict == "approved")
        corrected = sum(1 for f in feedbacks if f.verdict == "corrected")
        rejected_takeover = sum(1 for f in feedbacks if f.verdict in ("rejected", "manual_takeover"))

        # Verifier agreement rate strictly computed among decisions where verifier was used
        verified_feedbacks = [f for f in feedbacks if f.verifier_used]
        if verified_feedbacks:
            verified_approved = sum(1 for f in verified_feedbacks if f.verdict == "approved")
            verifier_agreement_rate: Optional[float] = round(verified_approved / len(verified_feedbacks), 3)
        else:
            verifier_agreement_rate = None

        transitions: List[Dict[str, Any]] = []
        trans_counts: Dict[tuple, int] = {}
        for f in feedbacks:
            if f.verdict == "corrected" and f.original_scenario and f.corrected_scenario:
                pair = (f.original_scenario, f.corrected_scenario)
                trans_counts[pair] = trans_counts.get(pair, 0) + 1

        for (orig, corr), cnt in trans_counts.items():
            transitions.append({"original_scenario": orig, "corrected_scenario": corr, "count": cnt})

        # Preflight failure rate strictly computed from RoutingPreflightRecord tied to filtered decisions
        stmt_pf = select(RoutingPreflightRecord).where(RoutingPreflightRecord.decision_id.in_(decision_ids))
        preflights = (await session.execute(stmt_pf)).scalars().all()
        total_pfs = len(preflights)
        failed_pfs = sum(1 for p in preflights if p.status in ("failed", "expired", "degraded", "missing"))
        preflight_failure_rate = round(failed_pfs / total_pfs, 3) if total_pfs > 0 else 0.0

        # Command execution stats strictly tied to filtered decisions
        stmt_cmd = select(CommandRecord).where(CommandRecord.decision_id.in_(decision_ids))
        commands = (await session.execute(stmt_cmd)).scalars().all()
        total_cmds = len(commands)
        succeeded_cmds = sum(1 for c in commands if c.status == "succeeded")
        failed_cmds = sum(1 for c in commands if c.status in ("failed", "needs_review"))

        # Latency percentiles strictly from real measurements
        if latencies:
            latencies.sort()
            n = len(latencies)
            p50_idx = int(0.50 * (n - 1))
            p95_idx = int(0.95 * (n - 1))
            p50_latency_ms: Optional[float] = round(latencies[p50_idx], 1)
            p95_latency_ms: Optional[float] = round(latencies[p95_idx], 1)
        else:
            p50_latency_ms = None
            p95_latency_ms = None

        return RoutingQualityMetricsDTO(
            total_decisions=total_decisions,
            by_routing_state=by_routing_state,
            approve_rate=round(approved / max(total_fb, 1), 3) if total_fb > 0 else 0.0,
            correction_rate=round(corrected / max(total_fb, 1), 3) if total_fb > 0 else 0.0,
            reject_takeover_rate=round(rejected_takeover / max(total_fb, 1), 3) if total_fb > 0 else 0.0,
            by_scenario=by_scenario,
            scenario_transitions=transitions,
            llm_verifier_call_rate=round(llm_calls / max(total_decisions, 1), 3),
            verifier_agreement_rate=verifier_agreement_rate,
            degraded_provider_rate=round(degraded_count / max(total_decisions, 1), 3),
            missing_facts_rate=round(missing_facts_count / max(total_decisions, 1), 3),
            preflight_failure_rate=preflight_failure_rate,
            command_success_rate=round(succeeded_cmds / max(total_cmds, 1), 3) if total_cmds > 0 else 0.0,
            command_failure_rate=round(failed_cmds / max(total_cmds, 1), 3) if total_cmds > 0 else 0.0,
            p50_latency_ms=p50_latency_ms,
            p95_latency_ms=p95_latency_ms,
        )

    async def batch_assign(
        self,
        ticket_ids: List[int],
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
    ) -> BatchAssignResponse:
        """Batch assign tickets to autopilot service bot and dispatch background tasks."""
        bot_user_id = getattr(settings, "BOT_USER_ID", None)
        bot_auth_b64 = None
        try:
            creds = await self.auth_bootstrap.bootstrap_auth(client=self.client, redis_client=redis_client)
            if not bot_user_id and creds.bot_user_id:
                bot_user_id = creds.bot_user_id
            bot_auth_b64 = creds.auth_b64
        except Exception as exc:
            logger.debug("Failed to bootstrap bot credentials for batch_assign: %s", exc)

        effective_auth = auth_b64 or bot_auth_b64

        assigned_count = 0
        failed_ids: List[int] = []
        details: Dict[int, str] = {}

        for tid in ticket_ids:
            try:
                task: TaskDTO = await self.client.get_task(task_id=tid, auth_b64=effective_auth)

                new_status_id: Optional[int] = None
                comment_text = "🤖 [Автопилот] Заявка передана на автоматическую обработку автопилоту (alen_assistant)."
                if task.status_id == 1:
                    new_status_id = 2

                await self.client.update_task(
                    task_id=tid,
                    status_id=new_status_id,
                    comment=comment_text,
                    executor_ids=str(bot_user_id) if bot_user_id is not None else None,
                    is_private=True,
                    auth_b64=effective_auth,
                )

                await TaskDispatchService.dispatch_autopilot_task(tid)

                assigned_count += 1
                details[tid] = "Успешно назначена на автопилот и поставлена в очередь воркера"
                logger.info("Ticket #%d batch-assigned to bot (executor: %s) and dispatched", tid, bot_user_id)
            except Exception as exc:
                failed_ids.append(tid)
                details[tid] = str(exc)
                logger.warning("Failed to batch-assign ticket #%d to autopilot: %s", tid, exc)

        return BatchAssignResponse(
            assigned_count=assigned_count,
            failed_ids=failed_ids,
            details=details,
        )

    async def reclaim_ticket(
        self,
        ticket_id: int,
        operator_username: str = "operator",
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Instantly reclaim ticket by human operator with cooperative worker cancellation."""
        if redis_client is not None:
            try:
                await redis_client.set(f"autopilot:abort:{ticket_id}", operator_username, ex=120)
                await redis_client.delete(
                    f"lock:task:{ticket_id}",
                    f"lock:autopilot:{ticket_id}",
                    f"cache:autopilot:plan:{ticket_id}",
                )
            except Exception as exc:
                logger.debug("Redis abort flag error for ticket #%d: %s", ticket_id, exc)

        note = (
            f"🛑 [Перехват оператором: {operator_username}]\n"
            f"Заявка снята с автопилота и взята в ручную обработку.\n"
            "Фоновые действия агента принудительно остановлены."
        )
        try:
            await self.client.update_task(
                task_id=ticket_id,
                comment=note,
                is_private=True,
                auth_b64=auth_b64,
            )
        except Exception as exc:
            logger.warning("Failed to leave reclaim note in ticket #%d: %s", ticket_id, exc)

        return {
            "status": "reclaimed",
            "ticket_id": ticket_id,
            "operator": operator_username,
        }
