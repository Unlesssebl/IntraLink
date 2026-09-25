"""Autopilot Service for Human-in-the-Loop Supervision and Harness Feedback."""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.src.core.ai import get_ai_client
from api.src.core.config import settings
from core.diagnostic.service import HostDiagnosticsService
from core.autopilot.policy_service import AutopilotPolicyService
from core.database.models import AutopilotCorrection, CommandRecord
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO
from core.intraservice.exceptions import IntraServiceNotFoundError
from core.scenarios.base import BaseScenario
from core.scenarios.engine import PlanSynthesizer
from core.scenarios.registry import get_default_scenario_registry

from .schemas import (
    AgentPlanDTO,
    ApprovePlanRequest,
    AutopilotCorrectionDTO,
    CorrectPlanRequest,
)

logger = logging.getLogger("api.features.autopilot.service")

PC_REGEX = re.compile(r"\b([A-Za-z0-9_-]*(?:wks|pc|ws|desktop|laptop)[A-Za-z0-9_-]*)\b", re.IGNORECASE)


class AutopilotService:
    """Service orchestrating agent plan evaluation, supervisor approvals and ground-truth corrections."""

    def __init__(
        self,
        client: Optional[IntraServiceClient] = None,
        policy_service: Optional[AutopilotPolicyService] = None,
        diagnostics_service: Optional[HostDiagnosticsService] = None,
    ) -> None:
        self.client = client or IntraServiceClient(
            base_url=settings.INTRASERVICE_URL,
            verify_ssl=settings.SSL_VERIFY,
        )
        self.policy_service = policy_service or AutopilotPolicyService()
        self.diagnostics_service = diagnostics_service or HostDiagnosticsService()

    async def get_agent_plan(
        self,
        ticket_id: int,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
    ) -> AgentPlanDTO:
        """Synthesize real-time evaluation plan of the autonomous agent for a ticket."""
        if redis_client is not None:
            cached_plan = await PlanSynthesizer.get_cached(ticket_id, redis_client)
            if cached_plan is not None:
                logger.debug("Serving agent plan for ticket #%d from Redis cache (0 ms)", ticket_id)
                return cached_plan

        try:
            task: TaskDTO = await self.client.get_task(task_id=ticket_id, auth_b64=auth_b64)
        except IntraServiceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ticket #{ticket_id} not found in IntraService",
            ) from exc

        # 1. Live Network Diagnostics (Express check of host)
        host_diag_dto = None
        target_host = task.entities.pc_name
        if not target_host:
            raw_text = f"{task.name} {task.description or ''}"
            matches = PC_REGEX.findall(raw_text)
            if matches:
                target_host = matches[0].strip().upper()
        if target_host:
            try:
                diag = await self.diagnostics_service.diagnose_host(hostname=target_host, redis_client=redis_client)
                host_diag_dto = diag.model_dump()
            except Exception as exc:
                logger.debug("Diagnostics for host %s failed: %s", target_host, exc)

        # 2. Check existing CommandRecord for this ticket (ASSISTED mode pending command)
        stmt = (
            select(CommandRecord)
            .where(CommandRecord.task_id == ticket_id)
            .order_by(desc(CommandRecord.created_at))
            .limit(1)
        )
        existing_cmd = (await session.execute(stmt)).scalar_one_or_none()
        command_id = existing_cmd.id if existing_cmd and existing_cmd.status == "pending" else None

        # 3. Check Dialogue Loop State (rounds count, waiting for applicant)
        dialogue_state = None
        last_event_id = None
        try:
            lifetimes: List[TaskLifetimeEventDTO] = await self.client.get_task_lifetime(
                task_id=ticket_id, auth_b64=auth_b64
            )
            rounds = sum(1 for e in lifetimes if e.status_id == 6)
            last_event_id = lifetimes[-1].id if lifetimes else None
            is_waiting = task.status_id == 6
            dialogue_state = {
                "rounds": rounds,
                "is_waiting_for_applicant": is_waiting,
                "total_events": len(lifetimes),
            }
        except Exception:
            pass

        # 4. Canonical plan synthesis via PlanSynthesizer
        registry = get_default_scenario_registry(ai_client=get_ai_client())
        synthesizer = PlanSynthesizer(registry=registry, policy_service=self.policy_service)
        plan = await synthesizer.synthesize_plan(
            task=task,
            host_diag=host_diag_dto,
            existing_command_id=command_id,
            dialogue_state=dialogue_state,
            last_event_id=last_event_id,
            session=session,
        )

        # 5. Cache in Redis
        if redis_client is not None:
            await PlanSynthesizer.store_cached(plan, redis_client, ttl=300)

        return plan

    async def approve_plan(
        self,
        ticket_id: int,
        req: ApprovePlanRequest,
        operator_username: str,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Approve agent plan: verify optimistic lock, dispatch Taskiq command, log positive feedback."""
        # 1. Optimistic Concurrency Check & OCC Version Guard
        task: TaskDTO = await self.client.get_task(task_id=ticket_id, auth_b64=auth_b64)
        if req.expected_status_id is not None and task.status_id != req.expected_status_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Статус заявки изменился с {req.expected_status_id} на {task.status_id} "
                    f"({task.status_name}) во время просмотра. План обновлен."
                ),
            )

        if req.last_event_id is not None:
            lifetimes: List[TaskLifetimeEventDTO] = await self.client.get_task_lifetime(
                task_id=ticket_id, auth_b64=auth_b64
            )
            current_last_event_id = lifetimes[-1].id if lifetimes else None
            if current_last_event_id is not None and current_last_event_id != req.last_event_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"История тикета изменилась во время рассмотрения (событие #{current_last_event_id} "
                        f"вместо #{req.last_event_id}). План обновлен."
                    ),
                )

        # 2. Find matching scenario
        registry = get_default_scenario_registry(ai_client=get_ai_client())
        scenario: Optional[BaseScenario] = await registry.find_scenario(task)
        action_name = scenario.scenario_key if scenario else "generic_action"

        # 3. Create or reuse CommandRecord
        idempotency_key = f"approved_{ticket_id}_{action_name}_{uuid.uuid4().hex[:6]}"
        cmd = CommandRecord(
            id=uuid.uuid4(),
            idempotency_key=idempotency_key,
            action=action_name,
            executor="api",
            target_json={"ticket_id": ticket_id, "pc_name": task.entities.pc_name},
            params_json={
                **task.entities.model_dump(),
                "override_comment": req.override_comment,
                "expected_status_id": req.expected_status_id,
            },
            status="pending",
            initiator=f"supervisor:{operator_username}",
            task_id=ticket_id,
        )
        session.add(cmd)
        await session.commit()
        await session.refresh(cmd)

        # Invalidate plan cache in Redis upon approval
        await PlanSynthesizer.invalidate(ticket_id, redis_client)

        # 4. Dispatch Taskiq task
        try:
            from api.src.core.task_dispatch import dispatch_command

            await dispatch_command(cmd.id)
        except Exception as exc:
            logger.warning("Failed to dispatch Taskiq task for approved command %s: %s", cmd.id, exc)

        logger.info("Supervisor %s approved plan for ticket #%d (command: %s)", operator_username, ticket_id, cmd.id)
        return {
            "status": "approved",
            "command_id": str(cmd.id),
            "ticket_id": ticket_id,
            "action": action_name,
        }

    async def correct_plan(
        self,
        ticket_id: int,
        req: CorrectPlanRequest,
        operator_username: str,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
        auth_b64: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record Ground-Truth delta in AutopilotCorrection and dispatch corrected Taskiq command."""
        # 1. Optimistic Concurrency Check & OCC Version Guard
        task: TaskDTO = await self.client.get_task(task_id=ticket_id, auth_b64=auth_b64)
        if req.expected_status_id is not None and task.status_id != req.expected_status_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Статус заявки изменился с {req.expected_status_id} на {task.status_id} "
                    f"({task.status_name}) во время просмотра. План обновлен."
                ),
            )

        if req.last_event_id is not None:
            lifetimes: List[TaskLifetimeEventDTO] = await self.client.get_task_lifetime(
                task_id=ticket_id, auth_b64=auth_b64
            )
            current_last_event_id = lifetimes[-1].id if lifetimes else None
            if current_last_event_id is not None and current_last_event_id != req.last_event_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"История тикета изменилась во время рассмотрения (событие #{current_last_event_id} "
                        f"вместо #{req.last_event_id}). План обновлен."
                    ),
                )

        # 2. Get baseline agent prediction
        registry = get_default_scenario_registry(ai_client=get_ai_client())
        original_scenario = await registry.find_scenario(task)
        orig_scenario_key = original_scenario.scenario_key if original_scenario else "unmatched"
        orig_confidence = 0.0
        if original_scenario:
            match_res = await original_scenario.evaluate_match(task)
            orig_confidence = match_res.confidence

        # 3. Check is_dirty (Edge Case 2.2: avoid phantom empty diffs)
        is_dirty = (
            orig_scenario_key != req.corrected_scenario
            or req.corrected_params != task.entities.model_dump()
            or bool(req.corrected_comment)
            or req.correction_tag != "general"
        )

        correction_id = None
        if is_dirty:
            # 4. Save to AutopilotCorrection (Ground-Truth dataset for Harness AI coder)
            correction = AutopilotCorrection(
                task_id=ticket_id,
                original_scenario=orig_scenario_key,
                corrected_scenario=req.corrected_scenario,
                original_params=task.entities.model_dump(),
                corrected_params=req.corrected_params,
                original_comment=None,
                corrected_comment=req.corrected_comment,
                confidence=orig_confidence,
                factors_snapshot={"original_scenario": orig_scenario_key, "is_dirty": True},
                correction_tag=req.correction_tag,
                operator_notes=req.operator_notes,
                operator_username=operator_username,
            )
            session.add(correction)
            await session.commit()
            await session.refresh(correction)
            correction_id = correction.id
            logger.info(
                "Recorded supervisor correction for ticket #%d: %s -> %s (tag: %s)",
                ticket_id,
                orig_scenario_key,
                req.corrected_scenario,
                req.correction_tag,
            )

        # 5. Dispatch corrected command
        idempotency_key = f"corrected_{ticket_id}_{req.corrected_scenario}_{uuid.uuid4().hex[:6]}"
        cmd = CommandRecord(
            id=uuid.uuid4(),
            idempotency_key=idempotency_key,
            action=req.corrected_scenario,
            executor="api",
            target_json={"ticket_id": ticket_id, "pc_name": req.corrected_params.get("pc_name")},
            params_json={
                **req.corrected_params,
                "override_comment": req.corrected_comment,
                "expected_status_id": req.expected_status_id,
            },
            status="pending",
            initiator=f"supervisor_corrected:{operator_username}",
            task_id=ticket_id,
        )
        session.add(cmd)
        await session.commit()
        await session.refresh(cmd)

        # Invalidate plan cache in Redis upon correction
        await PlanSynthesizer.invalidate(ticket_id, redis_client)

        try:
            from api.src.core.task_dispatch import dispatch_command

            await dispatch_command(cmd.id)
        except Exception as exc:
            logger.warning("Failed to dispatch Taskiq task for corrected command %s: %s", cmd.id, exc)

        return {
            "status": "corrected",
            "correction_id": str(correction_id) if correction_id else None,
            "command_id": str(cmd.id),
            "ticket_id": ticket_id,
            "action": req.corrected_scenario,
        }

    async def reclaim_ticket(
        self,
        ticket_id: int,
        operator_username: str,
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
            logger.warning("Failed to post reclaim audit note for ticket #%d: %s", ticket_id, exc)

        logger.info("Ticket #%d reclaimed by operator %s", ticket_id, operator_username)
        return {
            "status": "reclaimed",
            "ticket_id": ticket_id,
            "operator": operator_username,
            "message": "Тикет успешно перехвачен в ручную работу",
        }

    async def list_corrections(
        self,
        limit: int,
        tag: Optional[str],
        session: AsyncSession,
    ) -> List[AutopilotCorrectionDTO]:
        """Fetch historical corrections for inspection and dataset exports."""
        stmt = select(AutopilotCorrection).order_by(desc(AutopilotCorrection.created_at)).limit(limit)
        if tag:
            stmt = stmt.where(AutopilotCorrection.correction_tag == tag)
        res = await session.execute(stmt)
        records = res.scalars().all()
        return [
            AutopilotCorrectionDTO(
                id=r.id,
                task_id=r.task_id,
                original_scenario=r.original_scenario,
                corrected_scenario=r.corrected_scenario,
                original_params=r.original_params,
                corrected_params=r.corrected_params,
                original_comment=r.original_comment,
                corrected_comment=r.corrected_comment,
                confidence=r.confidence,
                factors_snapshot=r.factors_snapshot,
                correction_tag=r.correction_tag,
                operator_notes=r.operator_notes,
                operator_username=r.operator_username,
                created_at=r.created_at or datetime.now(timezone.utc),
            )
            for r in records
        ]

    async def export_corrections_jsonl(self, limit: int, session: AsyncSession) -> str:
        """Export Ground-Truth correction dataset in JSONL format for Harness AI coder."""
        corrections = await self.list_corrections(limit=limit, tag=None, session=session)
        lines = [json.dumps(c.model_dump(), default=str, ensure_ascii=False) for c in corrections]
        return "\n".join(lines)
