"""Ticket Analysis Service for Evidence-Based Routing Cascade.

Orchestrates ticket loading, snapshot creation, cascade decision evaluation,
read-only preflight verification, and persistent operator plan generation.
Enforces strict single-flight Redis locks during analysis and strictly read-only GETs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.autopilot.dto import (
    AgentPlanDTO,
    AutopilotPolicyDTO,
    EvidenceSummaryDTO,
)
from core.autopilot.intent import detect_tense_tone
from core.autopilot.policy_service import AutopilotPolicyService, get_policy_service
from core.database.models import (
    CommandRecord,
    PreparedPlanRecord,
    RoutingFeedbackRecord,
    RoutingPreflightRecord,
)
from core.database.session import get_engine, get_session_factory
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO, TaskLifetimeEventDTO
from core.intraservice.parser import extract_pc_names_from_text
from core.routing.composition import create_production_decision_service
from core.routing.contracts import RoutingDecision, RoutingState
from core.routing.lease import AnalysisLeaseError, RedisAnalysisLease
from core.routing.persistence import RoutingDecisionService
from core.routing.preflight import (
    RoutingPreflightService,
    compute_canonical_plan_hash,
    is_executable_preflight_status,
)
from core.routing.profile_registry import (
    get_default_profile_registry,
)
from core.routing.snapshot import TicketSnapshotFactory

logger = logging.getLogger("core.routing.analysis_service")

# Suggested comments and target status defaults per scenario
_SCENARIO_META: Dict[str, Dict[str, Any]] = {
    "ad_password_reset": {
        "name": "Сброс пароля Active Directory",
        "comment": "Данные для входа размещены в защищённых полях заявки.",
        "target_status_id": 3,
    },
    "account_lock": {
        "name": "Блокировка учетной записи (Увольнение)",
        "comment": "Учетная запись сотрудника заблокирована в соответствии с регламентом.",
        "target_status_id": 3,
    },
    "account_create": {
        "name": "Создание учетной записи Directum / AD",
        "comment": "Учетная запись пользователя успешно создана в Active Directory и Directum.",
        "target_status_id": 3,
    },
    "grant_wlan": {
        "name": "Предоставление доступа к корпоративному Wi-Fi (WLAN)",
        "comment": "Здравствуйте! Доступ к сети WLAN-WORKNET предоставлен для вашей учетной записи.",
        "target_status_id": 3,
    },
    "install_printer": {
        "name": "Установка и настройка принтера",
        "comment": "Принтер успешно установлен и настроен на вашем рабочем месте.",
        "target_status_id": 3,
    },
    "printer_spooler_restart": {
        "name": "Перезапуск службы диспетчера печати (Spooler)",
        "comment": "Служба диспетчера печати (Spooler) перезапущена. Очередь печати очищена.",
        "target_status_id": 3,
    },
    "default_printer_fix": {
        "name": "Назначение принтера по умолчанию",
        "comment": "Принтер по умолчанию переназначен на вашем рабочем месте.",
        "target_status_id": 3,
    },
    "offline_host": {
        "name": "Диагностика выключенного ПК",
        "comment": "Здравствуйте! Для выполнения заявки, пожалуйста, включите ваш рабочий компьютер и оставайтесь на связи.",
        "target_status_id": 6,
    },
    "service_redirect": {
        "name": "Перенаправление нецелевой заявки",
        "comment": "Заявка отменена, т. к. создана не в подходящем разделе каталога.\nТребуется оставить заявку в подходящем разделе каталога услуг.",
        "target_status_id": 30,
    },
    "rag_consultation": {
        "name": "Консультация по регламенту / Базе знаний",
        "comment": "Здравствуйте! По вашему вопросу направляем выдержку из регламента.",
        "target_status_id": 3,
    },
}


def build_evidence_summaries(decision: RoutingDecision) -> List[EvidenceSummaryDTO]:
    """Convert raw internal evidence records into safe, sanitized operator-facing summaries."""
    summaries: List[EvidenceSummaryDTO] = []
    for ev in decision.evidence:
        source_val = ev.source.value if hasattr(ev.source, "value") else str(ev.source)
        polarity_val = ev.polarity.value if hasattr(ev.polarity, "value") else str(ev.polarity)
        mapped_polarity = (
            "supporting"
            if polarity_val in ("supports", "supporting")
            else "contradicting"
            if polarity_val in ("contradicts", "contradicting")
            else "neutral"
        )

        reason_code = ev.source_ref.split(":")[0] if ":" in ev.source_ref else ev.source_ref
        strength_str = ev.strength.value if hasattr(ev.strength, "value") else str(ev.strength)
        desc = f"Доказательство: {source_val} ({strength_str})"
        if "catalog" in source_val:
            desc = "Точное совпадение сервиса в каталоге услуг IntraService"
        elif "lexical" in source_val:
            desc = "Обнаружены ключевые слова и фразы в тексте заявки"
        elif "semantic" in source_val:
            desc = "Семантическая близость текста заявки к эталонным примерам сценария"
        elif "verifier" in source_val or "llm" in source_val:
            desc = "Верификатор подтвердил применимость сценария"

        summaries.append(
            EvidenceSummaryDTO(
                source_type=source_val,
                reason_code=reason_code,
                description=desc,
                scenario_key=ev.candidate_key,
                verdict_polarity=mapped_polarity,
                provider=ev.source_ref,
                version=None,
                is_degraded=False,
            )
        )
    return summaries


class TicketAnalysisService:
    """Service orchestrating Evidence-Based Routing analysis, preflight and operator plan lifecycle."""

    def __init__(
        self,
        client: Optional[IntraServiceClient] = None,
        decision_service: Optional[RoutingDecisionService] = None,
        preflight_service: Optional[RoutingPreflightService] = None,
        policy_service: Optional[AutopilotPolicyService] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    ) -> None:
        self.client = client or IntraServiceClient()
        self.session_factory = session_factory
        self.decision_service = decision_service or create_production_decision_service(session_factory=session_factory)
        self.preflight_service = preflight_service or RoutingPreflightService()
        if policy_service is not None:
            self.policy_service = policy_service
        elif session_factory is not None:
            self.policy_service = AutopilotPolicyService(session_factory=session_factory)
        else:
            self.policy_service = get_policy_service()
        if self.session_factory is not None and getattr(self.policy_service, "session_factory", None) is None:
            self.policy_service.session_factory = self.session_factory
        self.profile_registry = get_default_profile_registry()

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self.session_factory is not None:
            return self.session_factory
        engine = get_engine()
        return get_session_factory(engine)

    async def get_plan_read_only(
        self,
        ticket_id: int,
        session: AsyncSession,
        redis_client: Optional[aioredis.Redis] = None,
    ) -> AgentPlanDTO:
        """Strictly read-only retrieval of the latest prepared plan for a ticket.

        Never triggers LLM, candidate evaluation, preflight execution, or mutations.
        """
        # 1. Redis fast path
        if redis_client is not None:
            try:
                cached_json = await redis_client.get(f"cache:autopilot:plan:{ticket_id}")
                if cached_json:
                    plan_dto = AgentPlanDTO.model_validate_json(cached_json)
                    # Supplement with live DB feedback/command state if session provided
                    await self._hydrate_live_status(plan_dto, ticket_id, session)
                    return plan_dto
            except Exception as exc:
                logger.debug("Redis plan get error for #%d: %s", ticket_id, exc)

        # 2. PostgreSQL prepared_plans query
        stmt = (
            select(PreparedPlanRecord)
            .where(PreparedPlanRecord.task_id == ticket_id)
            .order_by(desc(PreparedPlanRecord.created_at))
            .limit(1)
        )
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()

        if record is not None and record.plan_json:
            try:
                plan_dto = AgentPlanDTO.model_validate(record.plan_json)
                await self._hydrate_live_status(plan_dto, ticket_id, session)
                return plan_dto
            except Exception as exc:
                logger.warning("Failed to deserialize prepared plan for task #%d: %s", ticket_id, exc)

        # 3. Not analyzed default
        return AgentPlanDTO(
            task_id=ticket_id,
            scenario_key="unmatched",
            scenario_name="Анализ не выполнялся",
            description="План еще не сформирован. Нажмите «Анализировать» для запуска доказательного каскада.",
            analysis_state="not_analyzed",
            routing_state="unmatched",
            approval_state="not_applicable",
            can_approve=False,
            can_correct=False,
            can_reject=False,
        )

    async def _hydrate_live_status(self, plan_dto: AgentPlanDTO, ticket_id: int, session: AsyncSession) -> None:
        """Hydrate live command, feedback, and freshness indicators."""
        # Preflight expiration
        is_preflight_expired = False
        if plan_dto.preflight and plan_dto.preflight.expires_at:
            exp = plan_dto.preflight.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if datetime.now(UTC) > exp:
                plan_dto.preflight.is_expired = True
                is_preflight_expired = True
            plan_dto.freshness_expires_at = exp

        plan_dto.is_stale = is_preflight_expired

        # Check existing command
        stmt_cmd = (
            select(CommandRecord)
            .where(CommandRecord.task_id == ticket_id)
            .order_by(desc(CommandRecord.created_at))
            .limit(1)
        )
        cmd_rec = (await session.execute(stmt_cmd)).scalar_one_or_none()
        if cmd_rec:
            plan_dto.command_id = cmd_rec.id
            plan_dto.command_status = cmd_rec.status

        # Check existing terminal feedback
        stmt_fb = (
            select(RoutingFeedbackRecord)
            .where(RoutingFeedbackRecord.task_id == ticket_id)
            .order_by(desc(RoutingFeedbackRecord.created_at))
            .limit(1)
        )
        fb_rec = (await session.execute(stmt_fb)).scalar_one_or_none()
        if fb_rec:
            plan_dto.has_terminal_feedback = fb_rec.verdict in ("approved", "corrected", "rejected", "manual_takeover")
            if fb_rec.verdict == "approved":
                plan_dto.approval_state = "approved"
            elif fb_rec.verdict == "corrected":
                plan_dto.approval_state = "corrected"
            elif fb_rec.verdict == "rejected":
                plan_dto.approval_state = "rejected"
            elif fb_rec.verdict == "manual_takeover":
                plan_dto.approval_state = "manual_takeover"

        # Re-evaluate blocking reason codes & can_approve
        blocking = []
        if plan_dto.routing_state != "selected":
            blocking.append("not_selected")
        if plan_dto.missing_facts:
            blocking.append("missing_facts")
        if plan_dto.mode == "DISABLED":
            blocking.append("scenario_disabled")
        if plan_dto.is_circuit_broken:
            blocking.append("circuit_breaker_tripped")
        if not plan_dto.preflight:
            blocking.append("preflight_missing")
        elif plan_dto.preflight.is_expired:
            blocking.append("preflight_expired")
        elif plan_dto.preflight.status == "failed":
            blocking.append("preflight_failed")
        elif plan_dto.preflight.status == "degraded":
            blocking.append("preflight_degraded")
        if plan_dto.is_stale:
            blocking.append("plan_stale")
        if plan_dto.has_terminal_feedback:
            blocking.append("terminal_feedback_present")

        plan_dto.blocking_reason_codes = blocking
        plan_dto.can_approve = (
            plan_dto.routing_state == "selected"
            and plan_dto.is_executable
            and not plan_dto.is_stale
            and not plan_dto.has_terminal_feedback
            and plan_dto.mode != "DISABLED"
            and len(blocking) == 0
        )
        plan_dto.can_correct = not plan_dto.has_terminal_feedback
        plan_dto.can_reject = not plan_dto.has_terminal_feedback

        if not plan_dto.has_terminal_feedback:
            if plan_dto.can_approve:
                plan_dto.approval_state = "ready_for_approval"
            elif plan_dto.can_correct:
                plan_dto.approval_state = "blocked"
            else:
                plan_dto.approval_state = "not_applicable"

    async def analyze_ticket(
        self,
        ticket_id: int,
        force: bool = False,
        auth_b64: Optional[str] = None,
        session: Optional[AsyncSession] = None,
        redis_client: Optional[aioredis.Redis] = None,
    ) -> AgentPlanDTO:
        """Execute full Evidence Routing Cascade analysis for a ticket."""
        lease = RedisAnalysisLease(redis_client, f"routing:analysis:lock:{ticket_id}")
        if not await lease.acquire():
            logger.info("Analysis already in progress for ticket #%d", ticket_id)
            return AgentPlanDTO(
                task_id=ticket_id,
                scenario_key="unmatched",
                scenario_name="Анализ выполняется...",
                description="Выполняется доказательный анализ заявки...",
                analysis_state="in_progress",
                routing_state="unmatched",
                approval_state="not_applicable",
                can_approve=False,
                can_correct=False,
                can_reject=False,
            )
        lease.start_renewal()
        own_session = None

        try:
            if session is None:
                own_session = self._get_session_factory()()
                session = await own_session.__aenter__()

            # 1. Fetch fresh ticket and lifetime history
            task: TaskDTO = await self.client.get_task(task_id=ticket_id, auth_b64=auth_b64)
            lifetimes: List[TaskLifetimeEventDTO] = await self.client.get_task_lifetime(
                task_id=ticket_id, auth_b64=auth_b64
            )
            snapshot = TicketSnapshotFactory.create(task, comments=lifetimes)

            # 2. Check for fresh stored decision if not forced
            if not force and session is not None:
                stmt = (
                    select(PreparedPlanRecord)
                    .where(
                        PreparedPlanRecord.task_id == ticket_id,
                        PreparedPlanRecord.snapshot_hash == snapshot.snapshot_hash,
                    )
                    .order_by(desc(PreparedPlanRecord.created_at))
                    .limit(1)
                )
                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()
                if existing and existing.plan_json:
                    try:
                        plan_dto = AgentPlanDTO.model_validate(existing.plan_json)
                        await self._hydrate_live_status(plan_dto, ticket_id, session)
                        return plan_dto
                    except Exception as exc:
                        logger.debug("Failed parsing cached plan: %s", exc)

            # 3. Evaluate Evidence-Based Routing Cascade (pure evaluation, persistence is atomic at step 11)
            import time

            t_start = time.perf_counter()
            decision: RoutingDecision = await self.decision_service._cascade.decide(snapshot)
            duration_ms = round((time.perf_counter() - t_start) * 1000, 2)

            # 4. Resolve metadata
            scenario_key = decision.selected_scenario or "unmatched"
            profile = self.profile_registry.get(scenario_key)
            meta = _SCENARIO_META.get(scenario_key, {})

            scenario_name = meta.get("name") or (profile.intent_summary if profile else "Сценарий не определен")
            description = (profile.intent_summary if profile else "Заявка передана на ручную обработку")

            # Extract params from entities
            proposed_params: Dict[str, Any] = {}
            for k, v in snapshot.entities.items():
                if v and k not in ("it_password", "password", "token"):
                    proposed_params[k] = v

            suggested_comment = meta.get("comment", "")
            target_status_id = meta.get("target_status_id", 3)

            # 5. Extract PC candidate hosts
            raw_text = f"{snapshot.title} {snapshot.description or ''}"
            candidate_hosts = extract_pc_names_from_text(raw_text)
            if snapshot.entities.get("pc_name") and snapshot.entities.get("pc_name", "").upper() not in candidate_hosts:
                candidate_hosts.insert(0, snapshot.entities["pc_name"].upper())

            # 6. Sentiment & Attachments
            is_tense, tense_reason = detect_tense_tone(raw_text)
            has_attachments = len(snapshot.attachments) > 0

            # 7. Preflight validation
            preflight_dto = None
            if decision.state in (RoutingState.selected, RoutingState.needs_clarification):
                preflight_dto = await self.preflight_service.execute_preflight(
                    decision_id=decision.id,
                    snapshot=snapshot,
                    scenario_key=scenario_key,
                    params=proposed_params,
                    session=session,
                )

            # 8. Policy & Circuit Breaker
            policy: AutopilotPolicyDTO = await self.policy_service.get_policy(scenario_key, session=session)

            # 9. Compute Canonical Plan Hash & Plan ID
            plan_id = uuid.uuid4()
            plan_hash = compute_canonical_plan_hash(
                task_id=ticket_id,
                decision_id=decision.id,
                snapshot_hash=snapshot.snapshot_hash,
                scenario_key=scenario_key,
                proposed_params=proposed_params,
                suggested_comment=suggested_comment,
                target_status_id=target_status_id,
            )

            # 10. Assemble Evidence Summaries & Blocking Reasons
            evidence_summaries = build_evidence_summaries(decision)

            is_matched = decision.state == RoutingState.selected
            preflight_ok = (
                preflight_dto is not None
                and preflight_dto.id is not None
                and not preflight_dto.is_expired
                and is_executable_preflight_status(preflight_dto.status)
            )
            is_executable = is_matched and preflight_ok and not policy.is_circuit_broken and not decision.missing_facts

            blocking: List[str] = []
            if not is_matched:
                blocking.append("not_selected")
            if decision.missing_facts:
                blocking.append("missing_facts")
            if policy.mode == "DISABLED":
                blocking.append("scenario_disabled")
            if policy.is_circuit_broken:
                blocking.append("circuit_breaker_tripped")
            if not preflight_dto:
                blocking.append("preflight_missing")
            elif preflight_dto.is_expired:
                blocking.append("preflight_expired")
            elif preflight_dto.status == "failed":
                blocking.append("preflight_failed")
            elif preflight_dto.status == "degraded":
                blocking.append("preflight_degraded")

            can_approve = is_executable and policy.mode != "DISABLED" and len(blocking) == 0
            approval_state = "ready_for_approval" if can_approve else ("blocked" if is_matched else "not_applicable")

            plan_dto = AgentPlanDTO(
                task_id=ticket_id,
                scenario_key=scenario_key,
                scenario_name=scenario_name,
                description=description,
                analysis_state="ready",
                routing_state=decision.state.value,
                approval_state=approval_state,
                can_approve=can_approve,
                can_correct=True,
                can_reject=True,
                blocking_reason_codes=blocking,
                is_stale=False,
                freshness_expires_at=preflight_dto.expires_at if preflight_dto else None,
                has_terminal_feedback=False,
                decision_id=decision.id,
                snapshot_hash=snapshot.snapshot_hash,
                plan_id=plan_id,
                plan_hash=plan_hash,
                decision_reason_codes=list(decision.decision_reason_codes),
                degraded_components=dict(decision.degraded_components),
                missing_facts=list(decision.missing_facts),
                preflight=preflight_dto,
                is_executable=is_executable,
                evidence_summaries=evidence_summaries,
                extracted_entities=dict(snapshot.entities),
                candidate_hosts=candidate_hosts,
                proposed_action=scenario_key,
                proposed_params=proposed_params,
                suggested_comment=suggested_comment,
                target_status_id=target_status_id,
                last_event_id=snapshot.last_event_id,
                is_circuit_broken=policy.is_circuit_broken,
                mode=policy.mode,
                is_tense=is_tense,
                tense_reason=tense_reason,
                has_attachments=has_attachments,
            )

            # 11. Persist RoutingDecisionRecord, RoutingPreflightRecord & PreparedPlanRecord atomically
            preflight_record_id = None
            if preflight_dto is not None:
                preflight_record_id = preflight_dto.id
                preflight_expires = preflight_dto.expires_at
                if preflight_expires is not None and preflight_expires.tzinfo is None:
                    preflight_expires = preflight_expires.replace(tzinfo=UTC)
                preflight_record = RoutingPreflightRecord(
                    id=preflight_dto.id,
                    decision_id=decision.id,
                    task_id=ticket_id,
                    snapshot_hash=snapshot.snapshot_hash,
                    scenario_key=scenario_key,
                    params_hash=preflight_dto.params_hash,
                    status=preflight_dto.status,
                    checks_json=[c.model_dump(mode="json") for c in preflight_dto.checks],
                    details_json=preflight_dto.details,
                    error_message=preflight_dto.error_message,
                    expires_at=preflight_expires,
                )
            else:
                preflight_record = None

            plan_record = PreparedPlanRecord(
                id=plan_id,
                task_id=ticket_id,
                decision_id=decision.id,
                snapshot_hash=snapshot.snapshot_hash,
                plan_hash=plan_hash,
                scenario_key=scenario_key,
                preflight_id=preflight_record_id,
                plan_json=plan_dto.model_dump(mode="json"),
                state=decision.state.value,
            )

            decision_repo = self.decision_service._repository

            # Fail closed before any persistent write if renewal could not confirm ownership.
            await lease.ensure_owned()

            await decision_repo.insert(decision=decision, session=session, snapshot=snapshot, duration_ms=duration_ms)
            await session.flush()
            if preflight_record is not None:
                session.add(preflight_record)
                await session.flush()
            session.add(plan_record)
            await session.flush()
            await session.commit()

            # 12. Cache in Redis (ONLY after successful DB commit)
            if redis_client is not None:
                try:
                    await redis_client.set(
                        f"cache:autopilot:plan:{ticket_id}",
                        plan_dto.model_dump_json(),
                        ex=300,
                    )
                except Exception as exc:
                    logger.debug("Redis plan store error for #%d: %s", ticket_id, exc)

            return plan_dto

        finally:
            if own_session is not None:
                await own_session.__aexit__(None, None, None)
            await lease.stop_renewal()
            try:
                await lease.release()
            except AnalysisLeaseError as exc:
                logger.warning("Redis lease release failed for ticket #%d: %s", ticket_id, exc)
