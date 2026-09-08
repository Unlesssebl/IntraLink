"""Command resolution delivery and finalization boundary for IntraService."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import CommandEvent, CommandRecord, TicketRun, TicketRunEvent
from app.routers.deps import get_service_auth_b64
from app.services import intraservice
from app.services.command_secrets import CommandSecretService
from app.services.resolution_service import ResolutionUnavailable, resolve_outcome

logger = logging.getLogger(__name__)


def sanitize_error_code(raw_code: str | None, default: str = "execution_failed") -> str:
    """Очищает код ошибки для безопасной публичной публикации без секретов и путей."""
    if not raw_code:
        return default
    raw_str = str(raw_code).strip()
    if not raw_str or " " in raw_str:
        return default
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", raw_str)[:64].strip("_")
    if not cleaned or len(cleaned) < 2:
        return default
    lower = cleaned.lower()
    for sensitive in ("pass", "secret", "token", "key", "auth", "http", "traceback", "exception"):
        if sensitive in lower:
            return default
    return cleaned


async def _has_marker_in_task_history(auth_b64: str, task_id: int, marker: str) -> bool:
    """Проверяет наличие стабильного маркера в истории или комментариях заявки IntraService."""
    try:
        history = await intraservice.get_task_lifetime(auth_b64, task_id)
        if history and isinstance(history, list):
            for item in history:
                if not isinstance(item, dict):
                    continue
                text = str(
                    item.get("Comment")
                    or item.get("Comments")
                    or item.get("Description")
                    or item.get("Text")
                    or ""
                )
                if marker in text:
                    return True
        task_snapshot = await intraservice.get_single_task(auth_b64, task_id)
        if task_snapshot and isinstance(task_snapshot, dict):
            comments = task_snapshot.get("Comments") or task_snapshot.get("comments") or []
            if isinstance(comments, list):
                for c in comments:
                    text = str(c.get("Comment") or c.get("Comments") or c.get("Text") or "")
                    if marker in text:
                        return True
    except Exception as exc:
        logger.warning(
            "Не удалось проверить историю заявки %s на маркер %s: %s",
            task_id,
            marker,
            exc,
        )
    return False


class CommandDeliveryService:
    """Delivers verified command results to IntraService with one-time credentials."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def deliver_create_user(
        self,
        command_id: uuid.UUID,
        *,
        actor: str,
        service_auth_b64: str | None = None,
        operator_user_id: int | None = None,
        executor_ids: str | None = None,
    ) -> dict[str, Any]:
        command = await self.db.scalar(
            select(CommandRecord)
            .where(CommandRecord.id == command_id)
            .with_for_update()
        )
        if command is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Command not found")
        if command.action != "create_user":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Action '{command.action}' does not support credential delivery",
            )
        if command.status != "succeeded":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Command status is '{command.status}', expected 'succeeded'",
            )

        result = command.result_json if isinstance(command.result_json, dict) else {}
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        verified = bool(result.get("verified") or payload.get("verified"))
        if not verified:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Command execution verification missing or failed",
            )

        raw_task_id = command.task_id or (command.target_json or {}).get("task_id")
        try:
            task_id = int(raw_task_id) if raw_task_id is not None else None
        except (TypeError, ValueError):
            task_id = None
        if task_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Command has no associated IntraService task",
            )

        login = str(
            result.get("sam_account_name")
            or payload.get("sam_account_name")
            or result.get("user_principal_name")
            or payload.get("user_principal_name")
            or (command.params_json or {}).get("login")
            or ""
        )
        if not login:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Missing created user account login",
            )

        secret_service = CommandSecretService(self.db)
        artifact, temporary_password = await secret_service.peek(
            command_id, name="temporary_password"
        )

        try:
            resolution = await resolve_outcome(
                self.db,
                "user_created",
                {"login": login, "password": temporary_password},
                expected_kind="resolution",
            )
        except ResolutionUnavailable as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Failed to resolve resolution policy for user_created: {exc}",
            )

        auth = service_auth_b64
        if not auth:
            auth = await get_service_auth_b64(
                authorization=None, admin_session=None, token_query=None
            )

        op_user_id = operator_user_id or settings.PRIMARY_EXECUTOR_ID
        exec_ids = executor_ids or (
            str(op_user_id) if op_user_id else settings.DEFAULT_EXECUTOR_IDS
        )

        # 1. При необходимости переводим заявку в промежуточный статус 27 (В работе)
        try:
            task_snapshot = await intraservice.get_single_task(auth, task_id)
            current_status = (
                int(task_snapshot.get("StatusId"))
                if task_snapshot and task_snapshot.get("StatusId")
                else None
            )
        except Exception:
            current_status = None

        if current_status != 27:
            in_work_ok = await intraservice.update_task_full(
                auth_b64=auth,
                task_id=task_id,
                status_id=27,
                executor_ids=exec_ids,
            )
            if not in_work_ok:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    "Failed to transition task to intermediate 'in work' status in IntraService",
                )

        # 2. Переводим заявку в целевой статус 29 с комментарием и учетными данными
        target_status_id = resolution["status_id"] or 29
        upd_ok = await intraservice.update_task_full(
            auth_b64=auth,
            task_id=task_id,
            status_id=target_status_id,
            comment=resolution["comment"],
            executor_ids=exec_ids,
            is_private=False,
        )
        if not upd_ok:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Failed to publish resolution and finalize task in IntraService",
            )

        # 3. Списываем трудозатраты
        expenses = resolution.get("expenses") or 10
        if expenses > 0:
            await intraservice.add_task_expenses(
                auth_b64=auth,
                task_id=task_id,
                minutes=expenses,
                user_id=op_user_id,
            )

        # 4. Wipe секретного артефакта ТОЛЬКО после подтвержденной доставки
        await secret_service.wipe(artifact)
        temporary_password = ""

        # 5. Завершаем связанный TicketRun при наличии
        if command.ticket_run_id:
            run = await self.db.get(TicketRun, command.ticket_run_id)
            if run and run.completed_at is None:
                run.state = "completed"
                run.outcome = "completed"
                run.completed_at = artifact.consumed_at
                run.version += 1
                run.updated_by = actor
                run_seq = (
                    await self.db.scalar(
                        select(func.max(TicketRunEvent.sequence)).where(
                            TicketRunEvent.ticket_run_id == run.id
                        )
                    )
                    or 0
                ) + 1
                self.db.add(
                    TicketRunEvent(
                        ticket_run_id=run.id,
                        sequence=run_seq,
                        event_type="run_completed",
                        details_json={
                            "outcome": "completed",
                            "status_id": target_status_id,
                            "command_id": str(command.id),
                            "template_key": resolution["template_key"],
                        },
                        actor=actor,
                    )
                )

        # 6. Добавляем событие успешной доставки в историю команды
        cmd_seq = (
            await self.db.scalar(
                select(func.max(CommandEvent.sequence)).where(
                    CommandEvent.command_id == command.id
                )
            )
            or 0
        ) + 1
        self.db.add(
            CommandEvent(
                command_id=command.id,
                sequence=cmd_seq,
                event_type="delivery_succeeded",
                details_json={
                    "task_id": task_id,
                    "target_status_id": target_status_id,
                    "template_key": resolution["template_key"],
                    "artifact_id": str(artifact.id),
                },
                actor=actor,
            )
        )
        await self.db.commit()

        # 7. Возврат строго метаданных доставки (без пароля!)
        return {
            "command_id": str(command.id),
            "task_id": task_id,
            "status": "delivered",
            "target_status_id": target_status_id,
            "status_name": resolution["status_name"],
            "template_key": resolution["template_key"],
            "policy_version": resolution["policy_version"],
            "template_version": resolution["template_version"],
            "artifact_id": str(artifact.id),
            "consumed_at": (
                artifact.consumed_at.isoformat() if artifact.consumed_at else None
            ),
        }

    async def deliver_command_failure(
        self,
        command_id: uuid.UUID,
        *,
        actor: str,
        service_auth_b64: str | None = None,
    ) -> dict[str, Any]:
        """Безопасная доставка терминальной ошибки команды в IntraService без технических секретов."""
        command = await self.db.scalar(
            select(CommandRecord)
            .where(CommandRecord.id == command_id)
            .with_for_update()
        )
        if command is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Command not found")
        if command.status not in {"failed", "rejected", "cancelled", "needs_review"}:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Command status '{command.status}' is not eligible for failure delivery",
            )

        run = None
        if command.ticket_run_id:
            run = await self.db.scalar(
                select(TicketRun)
                .where(TicketRun.id == command.ticket_run_id)
                .with_for_update()
            )

        raw_task_id = command.task_id or (command.target_json or {}).get("task_id")
        if raw_task_id is None and run:
            raw_task_id = run.task_id
        try:
            task_id = int(raw_task_id) if raw_task_id is not None else None
        except (TypeError, ValueError):
            task_id = None

        if task_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Command has no associated IntraService task",
            )

        res_json = command.result_json if isinstance(command.result_json, dict) else {}
        extracted_code = res_json.get("error_code") or (run.error_code if run else None)

        if command.status == "needs_review":
            safe_error_code = sanitize_error_code(
                extracted_code, default="command_result_requires_review"
            )
            public_text = (
                "Результат автоматического выполнения не удалось подтвердить. "
                "Перед повторным запуском требуется проверить наличие учетной записи "
                f"в Active Directory. Код проверки: {safe_error_code}."
            )
        else:
            safe_error_code = sanitize_error_code(
                extracted_code, default=f"command_{command.status}"
            )
            public_text = (
                "При автоматической обработке заявки произошла ошибка. "
                "Автоматическое выполнение остановлено, заявка ожидает ручной проверки инженером. "
                f"Код ошибки: {safe_error_code}."
            )

        run_marker_id = run.id if run else command.id
        marker = f"[AUTOPILOT:{run_marker_id}:cmd_{command.id}_{command.version}_{command.status}]"
        full_comment = f"{public_text}\n\n{marker}"

        # 1. Проверяем собственное событие об успешной доставке
        already_delivered = False
        if run:
            has_run_event = await self.db.scalar(
                select(TicketRunEvent.id).where(
                    TicketRunEvent.ticket_run_id == run.id,
                    TicketRunEvent.event_type == "command_failure_delivery_succeeded",
                    TicketRunEvent.event_key == marker,
                )
            )
            if has_run_event:
                already_delivered = True
        else:
            has_cmd_event = await self.db.scalar(
                select(CommandEvent.id).where(
                    CommandEvent.command_id == command.id,
                    CommandEvent.event_type == "command_failure_delivery_succeeded",
                )
            )
            if has_cmd_event:
                already_delivered = True

        if already_delivered:
            return {
                "command_id": str(command.id),
                "task_id": task_id,
                "status": "already_delivered",
                "safe_error_code": safe_error_code,
                "marker": marker,
            }

        auth = service_auth_b64
        if not auth:
            try:
                auth = await get_service_auth_b64(
                    authorization=None, admin_session=None, token_query=None
                )
            except Exception:
                auth = None

        # 2. Если локальное событие отсутствует, проверяем историю в IntraService (на случай сбоя до commit)
        already_in_remote = False
        if auth:
            already_in_remote = await _has_marker_in_task_history(auth, task_id, marker)

        delivery_error = None
        if not auth:
            delivery_error = "service_auth_unavailable"
        elif not already_in_remote:
            try:
                # Оставляем/переводим заявку в статус 27 для ручной обработки инженером
                upd_ok = await intraservice.update_task_full(
                    auth_b64=auth,
                    task_id=task_id,
                    status_id=27,
                    comment=full_comment,
                    is_private=False,
                )
                if not upd_ok:
                    delivery_error = "IntraService update_task_full returned false"
            except Exception as exc:
                delivery_error = str(exc)

        # Логирование полных технических сведений только на сервере
        logger.info(
            "Доставка ошибки команды %s (status=%s, task=%s, safe_code=%s, technical_error=%s)",
            command.id,
            command.status,
            task_id,
            safe_error_code,
            command.error_message,
        )

        if delivery_error:
            logger.warning(
                "Сбой внешней доставки ошибки команды %s в заявку %s: %s",
                command.id,
                task_id,
                delivery_error,
            )
            if run:
                seq = (
                    await self.db.scalar(
                        select(func.max(TicketRunEvent.sequence)).where(
                            TicketRunEvent.ticket_run_id == run.id
                        )
                    )
                    or 0
                ) + 1
                self.db.add(
                    TicketRunEvent(
                        ticket_run_id=run.id,
                        sequence=seq,
                        event_type="command_failure_delivery_failed",
                        event_key=f"failed:{marker}:{seq}",
                        actor=actor,
                        details_json={
                            "command_id": str(command.id),
                            "task_id": task_id,
                            "safe_error_code": safe_error_code,
                            "error": delivery_error,
                        },
                    )
                )
            cmd_seq = (
                await self.db.scalar(
                    select(func.max(CommandEvent.sequence)).where(
                        CommandEvent.command_id == command.id
                    )
                )
                or 0
            ) + 1
            self.db.add(
                CommandEvent(
                    command_id=command.id,
                    sequence=cmd_seq,
                    event_type="command_failure_delivery_failed",
                    actor=actor,
                    details_json={
                        "task_id": task_id,
                        "marker": marker,
                        "safe_error_code": safe_error_code,
                        "error": delivery_error,
                    },
                )
            )
            await self.db.commit()
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Failed to deliver command failure to IntraService: {delivery_error}",
            )

        # Фиксация успешной доставки
        if run:
            seq = (
                await self.db.scalar(
                    select(func.max(TicketRunEvent.sequence)).where(
                        TicketRunEvent.ticket_run_id == run.id
                    )
                )
                or 0
            ) + 1
            self.db.add(
                TicketRunEvent(
                    ticket_run_id=run.id,
                    sequence=seq,
                    event_type="command_failure_delivery_succeeded",
                    event_key=marker,
                    actor=actor,
                    details_json={
                        "command_id": str(command.id),
                        "task_id": task_id,
                        "safe_error_code": safe_error_code,
                        "marker": marker,
                        "verified_by_history": already_in_remote,
                    },
                )
            )
        cmd_seq = (
            await self.db.scalar(
                select(func.max(CommandEvent.sequence)).where(
                    CommandEvent.command_id == command.id
                )
            )
            or 0
        ) + 1
        self.db.add(
            CommandEvent(
                command_id=command.id,
                sequence=cmd_seq,
                event_type="command_failure_delivery_succeeded",
                actor=actor,
                details_json={
                    "task_id": task_id,
                    "marker": marker,
                    "safe_error_code": safe_error_code,
                    "verified_by_history": already_in_remote,
                },
            )
        )
        await self.db.commit()
        return {
            "command_id": str(command.id),
            "task_id": task_id,
            "status": "delivered",
            "safe_error_code": safe_error_code,
            "marker": marker,
        }

    async def deliver_run_failure(
        self,
        run_id: uuid.UUID,
        *,
        error_code: str,
        error_message: str | None = None,
        actor: str,
        service_auth_b64: str | None = None,
    ) -> dict[str, Any]:
        """Безопасная доставка ошибки run (до создания команды) в IntraService."""
        run = await self.db.scalar(
            select(TicketRun)
            .where(TicketRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "TicketRun not found")

        task_id = run.task_id
        safe_error_code = sanitize_error_code(error_code, default="resolution_unavailable")
        public_text = (
            "При автоматической обработке заявки произошла ошибка. "
            "Автоматическое выполнение остановлено, заявка ожидает ручной проверки инженером. "
            f"Код ошибки: {safe_error_code}."
        )
        marker = f"[AUTOPILOT:{run.id}:run_{safe_error_code}_{run.version}]"
        full_comment = f"{public_text}\n\n{marker}"

        # 1. Проверяем локальное событие
        has_run_event = await self.db.scalar(
            select(TicketRunEvent.id).where(
                TicketRunEvent.ticket_run_id == run.id,
                TicketRunEvent.event_type == "run_failure_delivery_succeeded",
                TicketRunEvent.event_key == marker,
            )
        )
        if has_run_event:
            return {
                "run_id": str(run.id),
                "task_id": task_id,
                "status": "already_delivered",
                "safe_error_code": safe_error_code,
                "marker": marker,
            }

        auth = service_auth_b64
        if not auth:
            try:
                auth = await get_service_auth_b64(
                    authorization=None, admin_session=None, token_query=None
                )
            except Exception:
                auth = None

        # 2. Проверяем историю в IntraService
        already_in_remote = False
        if auth:
            already_in_remote = await _has_marker_in_task_history(auth, task_id, marker)

        delivery_error = None
        if not auth:
            delivery_error = "service_auth_unavailable"
        elif not already_in_remote:
            try:
                upd_ok = await intraservice.update_task_full(
                    auth_b64=auth,
                    task_id=task_id,
                    status_id=27,
                    comment=full_comment,
                    is_private=False,
                )
                if not upd_ok:
                    delivery_error = "IntraService update_task_full returned false"
            except Exception as exc:
                delivery_error = str(exc)

        logger.info(
            "Доставка ошибки run %s (task=%s, safe_code=%s, technical_error=%s)",
            run.id,
            task_id,
            safe_error_code,
            error_message or run.error_message,
        )

        if delivery_error:
            logger.warning(
                "Сбой внешней доставки ошибки run %s в заявку %s: %s",
                run.id,
                task_id,
                delivery_error,
            )
            seq = (
                await self.db.scalar(
                    select(func.max(TicketRunEvent.sequence)).where(
                        TicketRunEvent.ticket_run_id == run.id
                    )
                )
                or 0
            ) + 1
            self.db.add(
                TicketRunEvent(
                    ticket_run_id=run.id,
                    sequence=seq,
                    event_type="run_failure_delivery_failed",
                    event_key=f"failed:{marker}:{seq}",
                    actor=actor,
                    details_json={
                        "run_id": str(run.id),
                        "task_id": task_id,
                        "safe_error_code": safe_error_code,
                        "error": delivery_error,
                    },
                )
            )
            await self.db.commit()
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Failed to deliver run failure to IntraService: {delivery_error}",
            )

        seq = (
            await self.db.scalar(
                select(func.max(TicketRunEvent.sequence)).where(
                    TicketRunEvent.ticket_run_id == run.id
                )
            )
            or 0
        ) + 1
        self.db.add(
            TicketRunEvent(
                ticket_run_id=run.id,
                sequence=seq,
                event_type="run_failure_delivery_succeeded",
                event_key=marker,
                actor=actor,
                details_json={
                    "run_id": str(run.id),
                    "task_id": task_id,
                    "safe_error_code": safe_error_code,
                    "marker": marker,
                    "verified_by_history": already_in_remote,
                },
            )
        )
        await self.db.commit()
        return {
            "run_id": str(run.id),
            "task_id": task_id,
            "status": "delivered",
            "safe_error_code": safe_error_code,
            "marker": marker,
        }

    async def reconcile_undelivered_failure(
        self,
        run_id: uuid.UUID,
        *,
        actor: str = "poller",
        service_auth_b64: str | None = None,
    ) -> bool:
        """Повторный проход для недоставленных ошибок в paused или system_error состояниях."""
        run = await self.db.scalar(
            select(TicketRun).where(TicketRun.id == run_id)
        )
        if not run or run.completed_at is not None:
            return False

        # 1. Проверяем наличие терминальной ошибки команды
        latest_cmd = await self.db.scalar(
            select(CommandRecord)
            .where(CommandRecord.ticket_run_id == run.id)
            .order_by(CommandRecord.created_at.desc())
            .limit(1)
        )
        if latest_cmd and latest_cmd.status in {"failed", "rejected", "cancelled", "needs_review"}:
            has_succeeded = await self.db.scalar(
                select(TicketRunEvent.id).where(
                    TicketRunEvent.ticket_run_id == run.id,
                    TicketRunEvent.event_type == "command_failure_delivery_succeeded",
                )
            )
            if not has_succeeded:
                try:
                    await self.deliver_command_failure(
                        latest_cmd.id,
                        actor=actor,
                        service_auth_b64=service_auth_b64,
                    )
                    return True
                except Exception as exc:
                    logger.warning(
                        "Повторная доставка ошибки команды %s для run %s не удалась: %s",
                        latest_cmd.id,
                        run.id,
                        exc,
                    )
                    return False

        # 2. Проверяем ошибку уровня run (system_error)
        if run.state == "system_error":
            has_succeeded = await self.db.scalar(
                select(TicketRunEvent.id).where(
                    TicketRunEvent.ticket_run_id == run.id,
                    TicketRunEvent.event_type == "run_failure_delivery_succeeded",
                )
            )
            if not has_succeeded:
                try:
                    await self.deliver_run_failure(
                        run.id,
                        error_code=run.error_code or "system_error",
                        error_message=run.error_message,
                        actor=actor,
                        service_auth_b64=service_auth_b64,
                    )
                    return True
                except Exception as exc:
                    logger.warning(
                        "Повторная доставка ошибки run %s не удалась: %s",
                        run.id,
                        exc,
                    )
                    return False

        return False
