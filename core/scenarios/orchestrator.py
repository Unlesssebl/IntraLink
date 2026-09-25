"""ScenarioLifecycleOrchestrator: unified post-execution lifecycle for autopilot scenarios.

Eliminates duplicated execution + audit + cache-invalidation code present in:
  - worker/src/tasks/command_dispatcher.py (_execute_scenario_for_ticket, steps 6-9)
  - worker/src/tasks/autopilot.py (autopilot_task, steps 9-10)

Design contract:
  - Receives an already-fetched, entity-hydrated TaskDTO and a matched scenario.
  - Executes scenario.execute(task, policy).
  - Posts public resolution comment to IntraService.
  - Posts hidden technical audit note (IsPrivateComment=True).
  - Updates Circuit Breaker counters on success/failure via policy_service.
  - Invalidates Redis plan cache.
  - Returns the raw ScenarioExecutionResult for caller inspection.

The distributed concurrency lock (lock:task:{id}) and OCC checks remain in the
callers because they are context-specific (command_dispatcher uses cmd.params_json,
autopilot uses task lifetime rounds). The orchestrator handles only the invariant
post-lock execution steps.
"""

import logging
from typing import Optional

import redis.asyncio as aioredis

from core.autopilot.dto import AutopilotPolicyDTO
from core.autopilot.policy_service import AutopilotPolicyService
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import TaskDTO
from core.scenarios.base import BaseScenario, ExecutionAbortedException, ScenarioExecutionResult
from core.scenarios.engine import PlanSynthesizer

logger = logging.getLogger("core.scenarios.orchestrator")


class ScenarioLifecycleOrchestrator:
    """Executes a scenario and handles the full post-execution lifecycle.

    Invariant steps (identical across all callers):
      1. scenario.execute(task, policy)  →  ScenarioExecutionResult
      2. POST public resolution comment to IntraService.
      3. POST hidden technical audit note (IsPrivateComment=True).
      4. Record success/failure in AutopilotPolicyService (Circuit Breaker).
      5. Invalidate Redis plan cache.

    Usage (command_dispatcher context):
        orchestrator = ScenarioLifecycleOrchestrator(client, redis_conn, policy_service)
        exec_result = await orchestrator.execute_and_audit(
            scenario=scenario,
            task=task,
            policy=policy,
            auth_b64=auth.auth_b64,
            initiator=cmd.initiator,
            override_comment=cmd.params_json.get("override_comment"),
            update_circuit_breaker=False,   # dispatcher manages failures via cmd.status
        )

    Usage (autopilot context):
        orchestrator = ScenarioLifecycleOrchestrator(client, redis_conn, policy_service)
        exec_result = await orchestrator.execute_and_audit(
            scenario=scenario,
            task=task,
            policy=policy,
            auth_b64=auth.auth_b64,
            initiator="autopilot",
            update_circuit_breaker=True,    # autopilot actively manages CB counters
        )
        if exec_result.success:
            return {"status": "resolved", ...}
    """

    def __init__(
        self,
        client: IntraServiceClient,
        redis_conn: Optional[aioredis.Redis],
        policy_service: AutopilotPolicyService,
    ) -> None:
        self._client = client
        self._redis = redis_conn
        self._policy_service = policy_service

    async def execute_and_audit(
        self,
        scenario: BaseScenario,
        task: TaskDTO,
        policy: AutopilotPolicyDTO,
        auth_b64: str,
        initiator: str,
        *,
        override_comment: Optional[str] = None,
        update_circuit_breaker: bool = True,
    ) -> ScenarioExecutionResult:
        """Execute scenario and perform the full post-execution lifecycle.

        Args:
            scenario: Matched and validated BaseScenario instance.
            task: Pre-fetched, entity-hydrated TaskDTO.
            policy: Current autopilot policy for this scenario.
            auth_b64: Service bot Basic-auth credentials for IntraService API calls.
            initiator: Human-readable label for audit notes (e.g. "autopilot",
                "supervisor:username", "autopilot_assisted").
            override_comment: Operator-supplied override for the public resolution comment.
                If provided, takes priority over exec_result.resolution_comment.
            update_circuit_breaker: If True, record_success/record_failure is called
                on policy_service (used by autopilot). If False, the caller (command_dispatcher)
                manages CommandRecord status directly and does not need CB counters.

        Returns:
            ScenarioExecutionResult with success/failure details.
        """
        # Step 0: Cooperative Interruption (Reclaim) check
        if self._redis is not None:
            abort_key = f"autopilot:abort:{task.id}"
            try:
                if await self._redis.exists(abort_key):
                    logger.info(
                        "Orchestrator: execution cooperatively aborted for ticket #%d (abort flag active)",
                        task.id,
                    )
                    raise ExecutionAbortedException(
                        f"Execution aborted for ticket #{task.id}: reclaimed by operator"
                    )
            except ExecutionAbortedException:
                raise
            except Exception as exc:
                logger.debug("Redis abort check error in orchestrator for ticket #%d: %s", task.id, exc)

        try:
            exec_result: ScenarioExecutionResult = await scenario.execute(task, policy)
        except ExecutionAbortedException:
            raise
        except Exception as exc:
            logger.exception(
                "Scenario '%s' raised an unexpected exception for ticket #%d: %s",
                scenario.scenario_key,
                task.id,
                exc,
            )
            exec_result = ScenarioExecutionResult(
                success=False,
                action_taken=scenario.scenario_key,
                resolution_comment="",
                technical_note="",
                error=str(exc),
            )

        if exec_result.success:
            # Step 1: Post public resolution comment + status transition
            public_comment = override_comment or exec_result.resolution_comment
            await self._client.update_task(
                task_id=task.id,
                status_id=exec_result.target_status_id,
                comment=public_comment,
                is_private=False,
                auth_b64=auth_b64,
            )

            # Step 2: Post hidden technical audit note (Dual Audit pattern)
            audit_note = self._build_audit_note(
                scenario=scenario,
                exec_result=exec_result,
                initiator=initiator,
                success=True,
            )
            await self._client.update_task(
                task_id=task.id,
                comment=audit_note,
                is_private=True,
                auth_b64=auth_b64,
            )

            # Step 3: Update Circuit Breaker (success resets consecutive_failures)
            if update_circuit_breaker:
                await self._policy_service.record_success(scenario.scenario_key)

            # Step 4: Invalidate Redis plan cache
            await PlanSynthesizer.invalidate(task.id, self._redis)

            logger.info(
                "Orchestrator: scenario '%s' succeeded for ticket #%d (status → %d)",
                scenario.scenario_key,
                task.id,
                exec_result.target_status_id,
            )

        else:
            # Failure path: escalate + trip Circuit Breaker if requested
            logger.error(
                "Orchestrator: scenario '%s' failed for ticket #%d: %s",
                scenario.scenario_key,
                task.id,
                exec_result.error,
            )

            trip_alert = ""
            if update_circuit_breaker:
                updated_policy = await self._policy_service.record_failure(
                    scenario.scenario_key,
                    error=exec_result.error,
                )
                exec_result.metadata["circuit_broken"] = updated_policy.is_circuit_broken
                exec_result.metadata["policy"] = updated_policy
                trip_alert = (
                    "\n🚨 ПРЕДОХРАНИТЕЛЬ (Circuit Breaker): СРАБОТАЛ! Режим сценария понижен до ASSISTED."
                    if updated_policy.is_circuit_broken
                    else f"\nПоследовательных сбоев: {updated_policy.consecutive_failures}/3"
                )
            failure_note = (
                f"[Сбой исполнения сценария '{scenario.name}']\n"
                f"Машинная причина: {exec_result.error or 'unknown_error'}"
                f"{trip_alert}\n"
                "Заявка оставлена в статусе «В работе» для ручного исполнения инженером 1-й линии."
            )
            if exec_result.technical_note:
                failure_note += f"\nТехнические детали:\n{exec_result.technical_note}"
            await self._client.update_task(
                task_id=task.id,
                status_id=2,
                comment=failure_note,
                is_private=True,
                auth_b64=auth_b64,
            )
            await PlanSynthesizer.invalidate(task.id, self._redis)

        return exec_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_audit_note(
        scenario: BaseScenario,
        exec_result: ScenarioExecutionResult,
        initiator: str,
        success: bool,
    ) -> str:
        """Construct the hidden technical audit note for Helpdesk engineers."""
        if initiator == "autopilot" and exec_result.technical_note:
            return exec_result.technical_note
        note = (
            f"🤖 [Автопилот / Ко-пилот: Исполнение плана]\n"
            f"Сценарий: {scenario.name} ({scenario.scenario_key})\n"
            f"Одобрил: {initiator}\n"
            f"Исполнил: alen_assistant\n"
            f"Статус переведен в {exec_result.target_status_id}."
        )
        if exec_result.technical_note:
            note += f"\nТехнические детали: {exec_result.technical_note}"
        return note
