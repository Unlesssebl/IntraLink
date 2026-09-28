"""Durable command dispatcher for ADR 0006 capability execution."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.automation.capabilities import (
    CapabilityExecutionContext,
    CapabilityOutcome,
    CapabilityRegistry,
    get_default_capability_registry,
)
from core.automation.contracts import (
    ActionPlan,
    ActionPlanState,
    WorkflowPlan,
    WorkflowPlanState,
    compute_action_plan_hash,
)
from core.automation.persistence import canonical_params_hash
from core.automation.policy import CapabilityHealthService
from core.automation.runner import RunnerState, WorkflowRunner
from core.automation.snapshot import TicketSnapshotFactory
from core.database.models import (
    ActionPlanRecord,
    ActionPreflightRecord,
    CommandRecord,
    ExecutionFeedbackRecord,
    PlanFeedbackRecord,
    ServiceCatalogEntryRecord,
    ServiceCatalogVersionRecord,
    ServiceRouteBindingRecord,
    WorkflowPlanRecord,
    sanitize_secret_text,
    sanitize_secrets,
)
from core.database.session import get_engine, get_session_factory
from core.intraservice.auth import ServiceAuthBootstrap
from core.intraservice.client import IntraServiceClient
from core.redis_client import get_redis_client
from core.redis_lock import DistributedTaskLock, DistributedTaskLockOwnershipLost
from worker.src.broker import broker
from worker.src.tasks.sync_kb import sync_closed_tickets_task

logger = logging.getLogger("worker.tasks.command_dispatcher")
capability_health = CapabilityHealthService()

SystemActionHandler = Callable[[dict[str, Any], dict[str, Any], AsyncSession], Awaitable[dict[str, Any]]]
_SYSTEM_ACTIONS: dict[str, SystemActionHandler] = {}

_override_session_factory: async_sessionmaker[AsyncSession] | None = None
_override_client: IntraServiceClient | None = None
_override_service_auth: ServiceAuthBootstrap | None = None
_override_redis_client: aioredis.Redis | None = None
_override_capabilities: CapabilityRegistry | None = None


def set_session_factory(factory: async_sessionmaker[AsyncSession] | None) -> None:
    global _override_session_factory
    _override_session_factory = factory


set_dispatcher_session_factory = set_session_factory


def set_dispatcher_client(client: IntraServiceClient | None) -> None:
    global _override_client
    _override_client = client


def set_dispatcher_service_auth(auth: ServiceAuthBootstrap | None) -> None:
    global _override_service_auth
    _override_service_auth = auth


def set_dispatcher_redis_client(redis_client: aioredis.Redis | None) -> None:
    global _override_redis_client
    _override_redis_client = redis_client


def set_dispatcher_capability_registry(registry: CapabilityRegistry | None) -> None:
    global _override_capabilities
    _override_capabilities = registry


def _session_factory() -> async_sessionmaker[AsyncSession]:
    return _override_session_factory or get_session_factory(get_engine())


def _client() -> IntraServiceClient:
    return _override_client or IntraServiceClient()


def _service_auth() -> ServiceAuthBootstrap:
    return _override_service_auth or ServiceAuthBootstrap()


def _redis() -> aioredis.Redis | None:
    if _override_redis_client is not None:
        return _override_redis_client
    try:
        return get_redis_client()
    except Exception:
        return None


def _capabilities() -> CapabilityRegistry:
    return _override_capabilities or get_default_capability_registry()


def register_action(name: str) -> Callable[[SystemActionHandler], SystemActionHandler]:
    """Register infrastructure-only actions outside ticket automation."""

    def decorator(handler: SystemActionHandler) -> SystemActionHandler:
        _SYSTEM_ACTIONS[name] = handler
        return handler

    return decorator


@register_action("sync_kb")
async def _sync_kb(params: dict[str, Any], target: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    return await sync_closed_tickets_task(batch_size=int(params.get("batch_size", 100)))


@register_action("echo")
@register_action("test_action")
async def _echo(params: dict[str, Any], target: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    return {"status": "succeeded", "echo_params": params, "echo_target": target}


@register_action("cancel_duplicate")
@register_action("cancel_ticket")
async def _cancel_ticket(params: dict[str, Any], target: dict[str, Any], session: AsyncSession) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "ticket_id": target.get("ticket_id") or params.get("ticket_id"),
        "status_applied": 30,
        "public_comment": params.get("comment", "Отменена дублирующая заявка"),
    }


async def _load_bound_plan(session: AsyncSession, command: CommandRecord) -> tuple[ActionPlanRecord, ActionPlan, Any]:
    if command.action_plan_id is None or command.action_id is None or command.capability_key is None:
        raise ValueError("automation_command_binding_incomplete")
    record = await session.scalar(
        select(ActionPlanRecord).where(ActionPlanRecord.id == command.action_plan_id).with_for_update()
    )
    if record is None:
        raise ValueError("action_plan_not_found")
    plan = ActionPlan.model_validate(record.plan_json)
    if record.state not in {ActionPlanState.approved.value, ActionPlanState.running.value}:
        raise ValueError("action_plan_not_executable")
    if compute_action_plan_hash(plan) != record.plan_hash or command.plan_hash != record.plan_hash:
        raise ValueError("action_plan_hash_mismatch")
    if command.snapshot_hash != plan.snapshot_hash:
        raise ValueError("command_snapshot_binding_mismatch")
    action = next((item for item in plan.actions if item.id == command.action_id), None)
    if action is None or action.capability_key != command.capability_key:
        raise ValueError("command_action_binding_mismatch")
    expected_params_hash = canonical_params_hash(action.params)
    if (
        command.params_hash != expected_params_hash
        or canonical_params_hash(command.params_json or {}) != expected_params_hash
    ):
        raise ValueError("command_params_binding_mismatch")
    preflight = await session.scalar(
        select(ActionPreflightRecord)
        .where(
            ActionPreflightRecord.action_plan_id == plan.id,
            ActionPreflightRecord.action_id == action.id,
            ActionPreflightRecord.capability_key == action.capability_key,
            ActionPreflightRecord.snapshot_hash == plan.snapshot_hash,
            ActionPreflightRecord.plan_hash == plan.plan_hash,
            ActionPreflightRecord.params_hash == expected_params_hash,
            ActionPreflightRecord.status.in_(["passed", "not_applicable"]),
            ActionPreflightRecord.expires_at > datetime.now(UTC),
        )
        .order_by(ActionPreflightRecord.created_at.desc())
        .limit(1)
    )
    if preflight is None:
        raise ValueError("action_preflight_not_passed")
    return record, plan, action


async def _execute_capability(command: CommandRecord, session: AsyncSession) -> dict[str, Any]:
    plan_record, plan, action = await _load_bound_plan(session, command)
    terminal_feedback = await session.scalar(
        select(PlanFeedbackRecord)
        .where(
            PlanFeedbackRecord.action_plan_id == plan.id,
            PlanFeedbackRecord.verdict.in_(["rejected", "manual_takeover"]),
        )
        .limit(1)
    )
    if terminal_feedback is not None:
        return {"status": "aborted", "error": "manual_takeover"}

    registry = _capabilities()
    spec = registry.get(action.capability_key)
    executor = registry.get_executor(action.capability_key)
    if spec is None or not spec.enabled or executor is None:
        raise ValueError("capability_unavailable")

    redis_client = _redis()
    lock = DistributedTaskLock(redis_client, f"lock:task:{plan.task_id}", ttl_seconds=60)
    if not await lock.acquire():
        return {"status": "skipped", "error": "task_lease_unavailable"}

    mutation_started = False
    try:
        auth = await _service_auth().bootstrap_auth(client=_client(), redis_client=redis_client)
        task = await _client().get_task(task_id=plan.task_id, auth_b64=auth.auth_b64)
        if task.status_id in {3, 4, 30}:
            return {"status": "skipped", "error": "ticket_already_terminal"}
        if auth.bot_user_id is not None and auth.bot_user_id not in task.get_executor_ids():
            return {"status": "skipped", "error": "assigned_to_human"}
        lifetime = await _client().get_task_lifetime(task_id=plan.task_id, auth_b64=auth.auth_b64)
        current_snapshot = TicketSnapshotFactory.create(task=task, comments=lifetime)
        if current_snapshot.snapshot_hash != plan.snapshot_hash:
            return {"status": "skipped", "error": "stale_ticket_snapshot"}
        if action.capability_key == "create_ad_user":
            ad_gate_error = await _validate_ad_service_gate(
                session=session,
                plan=plan,
                command=command,
                task=task,
            )
            if ad_gate_error:
                return {"status": "skipped", "error": ad_gate_error}

        await lock.ensure_owned()
        await _set_workflow_state(session, plan.workflow_plan_id, WorkflowPlanState.executing)
        mutation_started = spec.is_mutating
        execution = await executor.execute(
            action.params,
            context=CapabilityExecutionContext(
                task_id=plan.task_id,
                action_plan_id=plan.id,
                command_id=command.id,
            ),
        )
        await lock.ensure_owned()

        proof = sanitize_secrets(execution.proof)
        await _set_workflow_state(session, plan.workflow_plan_id, WorkflowPlanState.verifying)
        is_last = action.sequence_no == max(item.sequence_no for item in plan.actions)
        if execution.outcome == CapabilityOutcome.succeeded:
            status_id = 3 if is_last else 2
            await _client().update_task(
                task_id=plan.task_id,
                status_id=status_id,
                comment=_success_comment(action.capability_key, proof),
                is_private=not is_last,
                auth_b64=auth.auth_b64,
            )
            if is_last:
                await _set_workflow_state(session, plan.workflow_plan_id, WorkflowPlanState.completed)
        else:
            await _client().update_task(
                task_id=plan.task_id,
                status_id=2,
                comment=_failure_note(action.capability_key, execution.error_code, execution.error_message, proof),
                is_private=True,
                auth_b64=auth.auth_b64,
            )
            await _set_workflow_state(session, plan.workflow_plan_id, WorkflowPlanState.needs_review)

        command_status = execution.outcome.value
        session.add(
            ExecutionFeedbackRecord(
                action_plan_id=plan.id,
                command_id=command.id,
                task_id=plan.task_id,
                action_id=action.id,
                capability_key=action.capability_key,
                outcome=command_status,
                result_json={"proof": proof, "error_code": execution.error_code},
            )
        )
        plan_record.state = ActionPlanState.running.value
        return {
            "status": command_status,
            "outcome": command_status,
            "proof": proof,
            "error": execution.error_code or execution.error_message,
        }
    except DistributedTaskLockOwnershipLost:
        if mutation_started:
            return {"status": "unknown_outcome", "error": "execution_ownership_lost"}
        raise
    except Exception:
        if mutation_started:
            logger.exception("Capability outcome became unknown after mutation started")
            return {"status": "unknown_outcome", "error": "post_mutation_failure"}
        raise
    finally:
        await lock.release()


async def _validate_ad_service_gate(
    *,
    session: AsyncSession,
    plan: ActionPlan,
    command: CommandRecord,
    task: Any,
) -> str | None:
    """Repeat every AD authorization binding immediately before LDAP mutation."""
    if (
        plan.service_binding_key != "ad_account_creation"
        or not plan.service_binding_version
        or not plan.catalog_hash
        or plan.source_service_id is None
    ):
        return "ad_service_not_authorized"
    if task.service_id != plan.source_service_id:
        return "ad_service_not_authorized"
    catalog = await session.scalar(
        select(ServiceCatalogVersionRecord).where(
            ServiceCatalogVersionRecord.is_active.is_(True),
            ServiceCatalogVersionRecord.validation_state == "validated",
        )
    )
    if catalog is None or catalog.catalog_hash != plan.catalog_hash:
        return "service_catalog_changed"
    binding = await session.scalar(
        select(ServiceRouteBindingRecord).where(
            ServiceRouteBindingRecord.key == plan.service_binding_key,
            ServiceRouteBindingRecord.version == plan.service_binding_version,
            ServiceRouteBindingRecord.catalog_hash == plan.catalog_hash,
            ServiceRouteBindingRecord.is_active.is_(True),
            ServiceRouteBindingRecord.is_validated.is_(True),
        )
    )
    if binding is None:
        return "service_binding_changed"
    if (
        task.service_id not in binding.service_ids_json
        or "employee_onboarding" not in binding.allowed_case_types_json
        or "employee_onboarding_workflow" not in binding.allowed_workflows_json
        or "create_ad_user" not in binding.allowed_capabilities_json
    ):
        return "ad_service_not_authorized"
    entry = await session.scalar(
        select(ServiceCatalogEntryRecord).where(
            ServiceCatalogEntryRecord.catalog_version_id == catalog.id,
            ServiceCatalogEntryRecord.service_id == task.service_id,
            ServiceCatalogEntryRecord.is_active.is_(True),
        )
    )
    if entry is None:
        return "ad_service_not_authorized"
    if binding.required_task_type_id is not None and task.task_type_id != binding.required_task_type_id:
        return "task_type_not_authorized"
    custom_fields = {str(key).lower(): str(value).strip() for key, value in (task.custom_fields or {}).items()}
    missing = [str(field) for field in binding.required_fields_json if not custom_fields.get(str(field).lower())]
    if missing:
        return "ad_required_form_fields_missing"
    prior_feedback = await session.scalar(
        select(ExecutionFeedbackRecord.id)
        .where(
            ExecutionFeedbackRecord.task_id == plan.task_id,
            ExecutionFeedbackRecord.capability_key == "create_ad_user",
            ExecutionFeedbackRecord.command_id != command.id,
            ExecutionFeedbackRecord.outcome.in_(["succeeded", "unknown_outcome", "partial"]),
        )
        .limit(1)
    )
    prior_command = await session.scalar(
        select(CommandRecord.id)
        .where(
            CommandRecord.task_id == plan.task_id,
            CommandRecord.capability_key == "create_ad_user",
            CommandRecord.id != command.id,
            CommandRecord.status.in_(["succeeded", "unknown_outcome", "running"]),
        )
        .limit(1)
    )
    if prior_feedback is not None or prior_command is not None:
        return "ad_previous_creation_not_safe_to_repeat"
    return None


async def _set_workflow_state(
    session: AsyncSession,
    workflow_plan_id: uuid.UUID,
    state: WorkflowPlanState,
) -> None:
    record = await session.get(WorkflowPlanRecord, workflow_plan_id)
    if record is None:
        raise ValueError("workflow_plan_not_found")
    plan = WorkflowPlan.model_validate(record.plan_json)
    updated = plan.model_copy(update={"state": state})
    record.state = state.value
    record.plan_json = updated.model_dump(mode="json")


def _success_comment(capability_key: str, proof: dict[str, Any]) -> str:
    if capability_key == "reset_print_spooler":
        return "Служба печати перезапущена, очередь очищена. Пожалуйста, повторите печать."
    if capability_key == "set_default_printer":
        return f"Принтер {proof.get('printer', '')} назначен принтером по умолчанию."
    if capability_key == "add_wlan_group_member":
        return "Доступ к корпоративной сети WLAN-WORKNET предоставлен."
    if capability_key == "create_ad_user":
        return "Учётная запись создана. Логин и временный пароль записаны в защищённые поля заявки."
    if capability_key == "disable_ad_user":
        return "Учётная запись сотрудника отключена, результат подтверждён повторным чтением Active Directory."
    return f"Техническое действие {capability_key} выполнено и проверено."


def _failure_note(
    capability_key: str,
    error_code: str | None,
    error_message: str | None,
    proof: dict[str, Any],
) -> str:
    safe_message = sanitize_secret_text(error_message or "")
    return (
        f"[Capability: {capability_key}] Результат не подтверждён. "
        f"Код: {error_code or 'unknown_outcome'}. Проверки: {proof}. "
        f"{safe_message} Заявка оставлена в статусе «В работе»."
    )


async def _advance_plan(action_plan_id: uuid.UUID, initiator: str) -> uuid.UUID | None:
    async with _session_factory()() as session, session.begin():
        advanced = await WorkflowRunner().advance(
            session,
            action_plan_id=action_plan_id,
            initiator=initiator,
        )
        if advanced.state == RunnerState.command_ready and advanced.command is not None:
            return advanced.command.id
    return None


@broker.task(task_name="dispatch_command_task")
async def dispatch_command_task(command_id: uuid.UUID | str) -> dict[str, Any]:
    target_id = uuid.UUID(command_id) if isinstance(command_id, str) else command_id
    next_command_id: uuid.UUID | None = None
    async with _session_factory()() as session:
        statement = select(CommandRecord).where(CommandRecord.id == target_id)
        command = await session.scalar(statement)
        if command is None:
            return {"command_id": str(target_id), "status": "failed", "error": "command_not_found"}
        claim = await session.execute(
            update(CommandRecord)
            .where(CommandRecord.id == target_id, CommandRecord.status == "pending")
            .values(status="running")
        )
        if claim.rowcount != 1:
            await session.rollback()
            command = await session.scalar(statement)
            return {
                "command_id": str(target_id),
                "status": command.status if command else "failed",
                "result": command.result_json if command else None,
                "error": command.error_message if command else "command_not_found",
            }
        await session.commit()
        command = await session.scalar(statement)
        assert command is not None

        try:
            if command.action_plan_id is not None:
                result = await _execute_capability(command, session)
            else:
                handler = _SYSTEM_ACTIONS.get(command.action)
                if handler is None:
                    raise ValueError("unbound_automation_command")
                result = await handler(command.params_json or {}, command.target_json or {}, session)
            status = str(result.get("status", "succeeded"))
            command.status = (
                status if status in {"succeeded", "failed", "unknown_outcome", "skipped", "aborted"} else "succeeded"
            )
            command.result_json = sanitize_secrets(result)
            command.error_message = sanitize_secret_text(str(result.get("error") or "")) or None
        except DistributedTaskLockOwnershipLost:
            command.status = "unknown_outcome"
            command.error_message = "execution_ownership_lost"
            command.result_json = {"error": "execution_ownership_lost"}
        except Exception as exc:
            logger.exception("Command %s failed", command.id)
            command.status = "failed"
            command.error_message = sanitize_secret_text(str(exc))
            command.result_json = sanitize_secrets({"error": str(exc)})
        if command.capability_key is not None:
            if command.status == "succeeded":
                await capability_health.record_success(session, capability_key=command.capability_key)
            elif command.status in {"failed", "unknown_outcome"}:
                await capability_health.record_failure(
                    session,
                    capability_key=command.capability_key,
                    outcome=command.status,
                    error_code=command.error_message,
                )
        await session.commit()
        action_plan_id = command.action_plan_id
        response = {
            "command_id": str(command.id),
            "status": command.status,
            "result": command.result_json,
            "error_message": command.error_message,
            "error": command.error_message,
        }

    if action_plan_id is not None:
        next_command_id = await _advance_plan(action_plan_id, command.initiator)
    if next_command_id is not None:
        await dispatch_command_task.kiq(command_id=str(next_command_id))
    return response
