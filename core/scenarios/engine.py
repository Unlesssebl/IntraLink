"""PlanSynthesizer: canonical agent plan synthesis from a pre-fetched TaskDTO.

Eliminates ~120 lines of duplicated plan synthesis logic that existed in both:
  - api/src/features/autopilot/service.py (AutopilotService.get_agent_plan)
  - worker/src/tasks/plan_prefetch.py (prefetch_agent_plan_task)

Design contract:
  - Pure synthesis: no network I/O, no auth bootstrapping.
  - Caller is responsible for fetching the TaskDTO and providing context-specific
    inputs (host_diag, dialogue_state, existing_command_id) because these differ
    between the API context (full DiagnosticsService) and the worker context (FastSocketProbe).
  - Redis caching is encapsulated via synthesize_and_cache() for atomic R/W.
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from core.autopilot.dto import AgentPlanDTO, AutopilotPolicyDTO
from core.autopilot.intent import detect_tense_tone
from core.autopilot.policy_service import AutopilotPolicyService
from core.intraservice.dto import TaskDTO
from core.intraservice.parser import PC_EXTRACT_REGEX, extract_pc_names_from_text
from core.scenarios.base import BaseScenario
from core.scenarios.registry import ScenarioRegistry

logger = logging.getLogger("core.scenarios.engine")

_PLAN_CACHE_KEY_PREFIX = "cache:autopilot:plan:"
_PLAN_CACHE_TTL_SEC = 300  # 5 minutes

# Suggested comment matrix: scenario_key → (comment, target_status_id)
# Canonical source of truth for default public resolution comments per scenario.
_SCENARIO_DEFAULTS: Dict[str, tuple[str, int]] = {
    "ad_password_reset": (
        "Здравствуйте! Ваш временный пароль для входа в домен: TempPass123! "
        "При первом входе система попросит сменить его.",
        3,
    ),
    "install_printer": (
        # pc_name will be interpolated by the synthesizer
        "__install_printer__",
        3,
    ),
    "grant_wlan": (
        "Здравствуйте! Доступ к сети WLAN-WORKNET предоставлен для вашей учетной записи.",
        3,
    ),
    "service_redirect": (
        "Заявка отменена, т. к. создана не в подходящем разделе каталога.\n"
        "Требуется оставить заявку в подходящем разделе каталога услуг.",
        30,
    ),
    "offline_host": (
        # pc_name will be interpolated by the synthesizer
        "__offline_host__",
        6,
    ),
}


class PlanSynthesizer:
    """Synthesizes AgentPlanDTO for a pre-fetched ticket.

    Usage (API context):
        synthesizer = PlanSynthesizer(registry, policy_service)
        plan = await synthesizer.synthesize_plan(
            task=task,
            host_diag=await diagnostics_service.diagnose_host(...),
            existing_command_id=pending_cmd.id,
            dialogue_state=...,
            last_event_id=...,
        )

    Usage (Worker prefetch context):
        synthesizer = PlanSynthesizer(registry, policy_service)
        plan = await synthesizer.synthesize_plan(task=task, host_diag=probe_result)
        await synthesizer.cache(plan, redis_conn)
    """

    def __init__(
        self,
        registry: ScenarioRegistry,
        policy_service: AutopilotPolicyService,
    ) -> None:
        self._registry = registry
        self._policy_service = policy_service

    async def synthesize_plan(
        self,
        task: TaskDTO,
        *,
        host_diag: Optional[Dict[str, Any]] = None,
        existing_command_id: Optional[UUID] = None,
        dialogue_state: Optional[Dict[str, Any]] = None,
        last_event_id: Optional[int] = None,
        session: Optional[AsyncSession] = None,
    ) -> AgentPlanDTO:
        """Synthesize a full AgentPlanDTO from a pre-fetched TaskDTO.

        Args:
            task: Fully-hydrated TaskDTO (entities already extracted).
            host_diag: Host diagnostic dict (caller-provided; format depends on
                context: full HostDiagnosticDTO.model_dump() from API or bare
                port probe dict from worker).
            existing_command_id: UUID of a pending CommandRecord for ASSISTED mode display.
            dialogue_state: Dict with rounds/is_waiting_for_applicant/total_events.
            last_event_id: ID of the last lifetime event for OCC version guard.
            session: Optional active AsyncSession to query policy without creating a new connection.

        Returns:
            AgentPlanDTO ready to be served to the UI or cached in Redis.
        """
        # 1. Candidate PC hosts extraction from raw text (union of fields + entity)
        raw_text = f"{task.name} {task.description or ''}"
        is_tense, tense_reason = detect_tense_tone(raw_text)
        has_attachments = bool(task.attachments)

        cleaned_candidates: List[str] = extract_pc_names_from_text(raw_text)
        if task.entities.pc_name and task.entities.pc_name.upper() not in cleaned_candidates:
            cleaned_candidates.insert(0, task.entities.pc_name.upper())

        # 2. Scenario discovery via Multi-Factor Router
        scenario: Optional[BaseScenario] = await self._registry.find_scenario(task)

        scenario_key = scenario.scenario_key if scenario else "unmatched"
        scenario_name = scenario.name if scenario else "Ручной разбор (сценарий не определен)"
        description = scenario.description if scenario else "Заявка передана на ручную классификацию оператору"

        # 3. Policy, circuit breaker and mode
        policy: AutopilotPolicyDTO = await self._policy_service.get_policy(scenario_key, session=session)
        is_circuit_broken = policy.is_circuit_broken
        mode = policy.mode

        # 4. Evaluation match and preconditions
        confidence = 0.0
        factor_breakdown: Dict[str, float] = {}
        preconditions_dict: Dict[str, Any] = {
            "is_valid": False,
            "missing_facts": [],
            "environment_barriers": [],
        }
        suggested_comment = ""
        target_status_id = 3

        if scenario:
            match_res = await scenario.evaluate_match(task)
            confidence = match_res.confidence
            factor_breakdown = {"confidence": confidence}

            precond_res = await scenario.validate_preconditions(task)
            preconditions_dict = precond_res.model_dump()

            # 5. Derive suggested comment & target status:
            # FUNDAMENTAL RULE: If preconditions failed, the agent MUST NOT generate
            # a completion comment (Status 3). Instead, it MUST prompt the applicant
            # for missing facts / environmental actions and suggest Status 6 (Suspended).
            if not precond_res.is_valid:
                target_status_id = 6  # Приостановлена (Ожидание ответа заявителя)
                suggested_comment = precond_res.clarification_prompt
                if not suggested_comment and scenario.definition and scenario.definition.clarification_template:
                    suggested_comment = scenario.definition.clarification_template
                if not suggested_comment:
                    target_status_id = 2  # В работе (Передано инженеру)
                    suggested_comment = "Заявка передана на ручную обработку дежурному инженеру."
            else:
                suggested_comment, target_status_id = self._resolve_success_comment(
                    scenario_key=scenario_key,
                    pc_name=task.entities.pc_name,
                    task=task,
                )

        return AgentPlanDTO(
            task_id=task.id,
            scenario_key=scenario_key,
            scenario_name=scenario_name,
            description=description,
            confidence=confidence,
            matched=scenario is not None and confidence >= policy.min_confidence,
            factor_breakdown=factor_breakdown,
            preconditions=preconditions_dict,
            host_diagnostic=host_diag,
            extracted_entities=task.entities.model_dump(),
            candidate_hosts=cleaned_candidates,
            proposed_action=scenario_key,
            proposed_params=task.entities.model_dump(),
            suggested_comment=suggested_comment,
            target_status_id=target_status_id,
            dialogue_state=dialogue_state,
            command_id=existing_command_id,
            last_event_id=last_event_id,
            is_circuit_broken=is_circuit_broken,
            mode=mode,
            is_tense=is_tense,
            tense_reason=tense_reason,
            has_attachments=has_attachments,
        )

    # ------------------------------------------------------------------
    # Redis cache helpers
    # ------------------------------------------------------------------

    @staticmethod
    def cache_key(ticket_id: int) -> str:
        """Return the canonical Redis cache key for a given ticket's plan."""
        return f"{_PLAN_CACHE_KEY_PREFIX}{ticket_id}"

    @staticmethod
    async def get_cached(ticket_id: int, redis_client: aioredis.Redis) -> Optional[AgentPlanDTO]:
        """Read-through: return cached AgentPlanDTO or None on miss/error."""
        key = PlanSynthesizer.cache_key(ticket_id)
        try:
            raw = await redis_client.get(key)
            if raw:
                logger.debug("Plan cache HIT for ticket #%d (0 ms)", ticket_id)
                return AgentPlanDTO.model_validate_json(raw)
        except Exception as exc:
            logger.debug("Plan cache read error for ticket #%d: %s", ticket_id, exc)
        return None

    @staticmethod
    async def store_cached(
        plan: AgentPlanDTO,
        redis_client: aioredis.Redis,
        ttl: int = _PLAN_CACHE_TTL_SEC,
    ) -> None:
        """Write plan to Redis cache with TTL."""
        key = PlanSynthesizer.cache_key(plan.task_id)
        try:
            await redis_client.set(key, plan.model_dump_json(), ex=ttl)
            logger.debug("Plan cached for ticket #%d (TTL %ds)", plan.task_id, ttl)
        except Exception as exc:
            logger.debug("Plan cache write error for ticket #%d: %s", plan.task_id, exc)

    @staticmethod
    async def invalidate(ticket_id: int, redis_client: Optional[aioredis.Redis]) -> None:
        """Evict cached plan on ticket mutation (approve/correct/execute)."""
        if redis_client is None:
            return
        try:
            await redis_client.delete(PlanSynthesizer.cache_key(ticket_id))
        except Exception as exc:
            logger.debug("Plan cache invalidation error for ticket #%d: %s", ticket_id, exc)

    async def synthesize_and_cache(
        self,
        task: TaskDTO,
        redis_client: Optional[aioredis.Redis],
        *,
        host_diag: Optional[Dict[str, Any]] = None,
        existing_command_id: Optional[UUID] = None,
        dialogue_state: Optional[Dict[str, Any]] = None,
        last_event_id: Optional[int] = None,
        session: Optional[AsyncSession] = None,
        ttl: int = _PLAN_CACHE_TTL_SEC,
    ) -> AgentPlanDTO:
        """Read-through: return cached plan or synthesize, store, and return.

        Provides atomic cache-miss path for plan_prefetch and get_agent_plan.
        """
        if redis_client is not None:
            cached = await self.get_cached(task.id, redis_client)
            if cached is not None:
                return cached

        plan = await self.synthesize_plan(
            task,
            host_diag=host_diag,
            existing_command_id=existing_command_id,
            dialogue_state=dialogue_state,
            last_event_id=last_event_id,
            session=session,
        )

        if redis_client is not None:
            await self.store_cached(plan, redis_client, ttl=ttl)

        return plan

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_success_comment(
        scenario_key: str,
        pc_name: Optional[str],
        task: Optional[TaskDTO] = None,
    ) -> tuple[str, int]:
        """Return (suggested_comment, target_status_id) upon successful execution.

        Handles pc_name interpolation for install_printer and offline_host.
        Differentiates USB vs Network printer resolution message.
        """
        entry = _SCENARIO_DEFAULTS.get(scenario_key)
        if entry is None:
            return "", 3

        comment_template, target_status_id = entry

        if comment_template == "__install_printer__":
            full_text = f"{task.name if task else ''} {task.description if task else ''}".lower()
            is_usb = any(kw in full_text for kw in ("usb", "юсб", "шнур", "кабел", "провод", "локальн"))
            if is_usb:
                return (
                    (
                        f"Здравствуйте! Драйвер принтера успешно установлен на вашем компьютере {pc_name or ''}. "
                        "Пожалуйста, выполните пробную печать документа. При возникновении вопросов ответьте на это сообщение."
                    ),
                    target_status_id,
                )
            return (
                (
                    f"Здравствуйте! Сетевой принтер настроен на вашем рабочем месте {pc_name or ''}. "
                    "Отправлена тестовая страница."
                ),
                target_status_id,
            )

        if comment_template == "__offline_host__":
            return (
                (
                    f"Здравствуйте! Компьютер {pc_name or ''} недоступен в корпоративной сети. "
                    "Пожалуйста, включите ПК и оставьте ответный комментарий — настройка продолжится автоматически."
                ),
                target_status_id,
            )

        return comment_template, target_status_id

    @staticmethod
    def _resolve_suggested_comment(
        scenario_key: str,
        pc_name: Optional[str],
    ) -> tuple[str, int]:
        """Backwards compatibility alias for _resolve_success_comment."""
        return PlanSynthesizer._resolve_success_comment(scenario_key, pc_name)
