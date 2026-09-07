"""Command resolution delivery and finalization boundary for IntraService."""

from __future__ import annotations

import logging
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
