"""Command Dispatcher executing CommandRecord lifecycle via Taskiq."""

import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.database.models import CommandRecord
from core.database.session import get_engine, get_session_factory
from worker.src.broker import broker
from worker.src.tasks.ad_actions import reset_ad_password_task, unlock_ad_account_task
from worker.src.tasks.printers import install_printer_task
from worker.src.tasks.sync_kb import sync_closed_tickets_task

logger = logging.getLogger("worker.tasks.command_dispatcher")

# Registry of action handlers: action_name -> async func(params, target, session) -> dict
ActionHandler = Callable[[Dict[str, Any], Dict[str, Any], AsyncSession], Awaitable[Dict[str, Any]]]
_ACTION_REGISTRY: Dict[str, ActionHandler] = {}

# Session factory hook (allows test overrides)
_override_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def set_session_factory(factory: Optional[async_sessionmaker[AsyncSession]]) -> None:
    """Override session factory for testing environments."""
    global _override_session_factory
    _override_session_factory = factory


def _get_active_session_factory() -> async_sessionmaker[AsyncSession]:
    if _override_session_factory is not None:
        return _override_session_factory
    engine = get_engine()
    return get_session_factory(engine)


def register_action(action_name: str) -> Callable[[ActionHandler], ActionHandler]:
    """Decorator to register an action execution handler."""

    def decorator(func: ActionHandler) -> ActionHandler:
        _ACTION_REGISTRY[action_name] = func
        return func

    return decorator


# --- Built-in Action Handlers ---


@register_action("install_printer")
async def _handle_install_printer(
    params: Dict[str, Any],
    target: Dict[str, Any],
    session: AsyncSession,
) -> Dict[str, Any]:
    host = params.get("pc_name") or params.get("host") or ""
    printer_name = params.get("printer_name") or params.get("printer") or ""
    if not host or not printer_name:
        raise ValueError(f"install_printer requires 'pc_name'/'host' and 'printer_name', got: {params}")
    return await install_printer_task(host=host, printer_name=printer_name)


@register_action("ad_password_reset")
@register_action("reset_ad_password")
async def _handle_ad_password_reset(
    params: Dict[str, Any],
    target: Dict[str, Any],
    session: AsyncSession,
) -> Dict[str, Any]:
    account = params.get("sam_account_name") or params.get("account") or params.get("username") or ""
    if not account:
        raise ValueError(f"ad_password_reset requires 'sam_account_name' or 'username', got: {params}")
    return await reset_ad_password_task(sam_account_name=account)


@register_action("ad_account_unlock")
@register_action("unlock_ad_account")
async def _handle_ad_account_unlock(
    params: Dict[str, Any],
    target: Dict[str, Any],
    session: AsyncSession,
) -> Dict[str, Any]:
    account = params.get("sam_account_name") or params.get("account") or params.get("username") or ""
    if not account:
        raise ValueError(f"ad_account_unlock requires 'sam_account_name' or 'username', got: {params}")
    return await unlock_ad_account_task(sam_account_name=account)


@register_action("sync_kb")
async def _handle_sync_kb(
    params: Dict[str, Any],
    target: Dict[str, Any],
    session: AsyncSession,
) -> Dict[str, Any]:
    batch_size = int(params.get("batch_size", 100))
    return await sync_closed_tickets_task(batch_size=batch_size)


@register_action("echo")
@register_action("test_action")
async def _handle_echo(
    params: Dict[str, Any],
    target: Dict[str, Any],
    session: AsyncSession,
) -> Dict[str, Any]:
    return {"status": "succeeded", "echo_params": params, "echo_target": target}


@register_action("cancel_duplicate")
@register_action("cancel_ticket")
async def _handle_cancel_ticket(
    params: Dict[str, Any],
    target: Dict[str, Any],
    session: AsyncSession,
) -> Dict[str, Any]:
    ticket_id = target.get("ticket_id") or params.get("ticket_id")
    comment = params.get("comment", "Отменена дублирующая заявка")
    return {
        "status": "succeeded",
        "ticket_id": ticket_id,
        "status_applied": 30,
        "public_comment": comment,
    }


# --- Taskiq Dispatcher Task ---


@broker.task(task_name="dispatch_command_task")
async def dispatch_command_task(command_id: Union[uuid.UUID, str]) -> Dict[str, Any]:
    """Execute CommandRecord by command_id: pending -> running -> succeeded / failed."""
    if isinstance(command_id, str):
        target_uuid = uuid.UUID(command_id)
    else:
        target_uuid = command_id

    session_factory = _get_active_session_factory()

    async with session_factory() as session:
        stmt = select(CommandRecord).where(CommandRecord.id == target_uuid)
        cmd: Optional[CommandRecord] = (await session.execute(stmt)).scalar_one_or_none()

        if cmd is None:
            logger.error("CommandRecord %s not found in database.", target_uuid)
            return {"command_id": str(target_uuid), "status": "failed", "error": "Command not found"}

        # Idempotency check: if already completed, do not re-run
        if cmd.status in ("succeeded", "failed"):
            logger.info("CommandRecord %s is already terminal (%s). Skipping.", target_uuid, cmd.status)
            return {
                "command_id": str(target_uuid),
                "status": cmd.status,
                "result": cmd.result_json,
                "error_message": cmd.error_message,
            }

        # Transition: pending -> running
        cmd.status = "running"
        await session.commit()
        await session.refresh(cmd)
        logger.info("Executing CommandRecord %s (action: %s, initiator: %s)", cmd.id, cmd.action, cmd.initiator)

        handler = _ACTION_REGISTRY.get(cmd.action)
        if handler is None:
            err_msg = f"Unknown action: '{cmd.action}'. Registered actions: {list(_ACTION_REGISTRY.keys())}"
            logger.error(err_msg)
            cmd.status = "failed"
            cmd.error_message = err_msg
            cmd.result_json = {"error": err_msg, "failure_kind": "unknown_action"}
            await session.commit()
            return {"command_id": str(target_uuid), "status": "failed", "error": err_msg}

        try:
            result = await handler(cmd.params_json or {}, cmd.target_json or {}, session)
            cmd.status = "succeeded"
            cmd.result_json = result
            cmd.error_message = None
            logger.info("CommandRecord %s succeeded.", cmd.id)
        except Exception as exc:
            logger.exception("CommandRecord %s execution failed: %s", cmd.id, exc)
            cmd.status = "failed"
            cmd.error_message = str(exc)
            cmd.result_json = {"error": str(exc), "failure_kind": "execution_error"}

        await session.commit()
        await session.refresh(cmd)

        return {
            "command_id": str(cmd.id),
            "status": cmd.status,
            "result": cmd.result_json,
            "error_message": cmd.error_message,
        }
