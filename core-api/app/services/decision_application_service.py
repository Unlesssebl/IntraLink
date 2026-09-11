"""
Сервис управления жизненным циклом применения решений (Этап 5 Roadmap).
Обеспечивает:
- Идемпотентность пакетного и одиночного применения через DecisionApplicationRequest.
- Двухфазное управление попытками DecisionApplicationAttempt (pending -> running -> outcome).
- Структурированные результаты подопераций IntraService без скрытых сетевых повторов.
- Атомарную проекцию в DecisionApplication и расчет DecisionFeedback.
- Изоляцию dry_run от мутаций и аудита.
- Сверку (Reconciliation) неопределенных результатов (unknown).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import (
    DecisionApplication,
    DecisionApplicationAttempt,
    DecisionApplicationRequest,
    DecisionFeedback,
    DecisionRecord,
    DecisionResponseVariant,
    TicketRun,
)
from app.services import intraservice
from app.services.decision_journal import (
    DecisionJournalService,
    calculate_comment_diff,
    sanitize_payload,
    ticket_snapshot_fingerprint,
)
from app.services.intraservice import MutationOutcome
from app.services.safety import DeadMansSwitchError, enforce_triage_apply_rate_limit
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.services.decision_application")


def calculate_request_hash(
    *,
    task_ids: list[int],
    status_id: int,
    comment: str,
    expenses: int,
    executor_ids: str | None,
    is_private: bool,
    decision_bindings: list[dict[str, Any]] | None = None,
    feedback_reason_code: str | None = None,
    feedback_comment: str | None = None,
) -> str:
    """Детерминированный хэш канонического представления запроса на применение."""
    sorted_tasks = sorted(set(task_ids))
    sorted_bindings = sorted(
        decision_bindings or [],
        key=lambda b: int(b.get("task_id", 0)),
    )
    material = {
        "task_ids": sorted_tasks,
        "status_id": status_id,
        "comment": comment.replace("\r\n", "\n").replace("\r", "\n").strip(),
        "expenses": expenses,
        "executor_ids": executor_ids or "",
        "is_private": is_private,
        "bindings": sorted_bindings,
        "feedback_reason_code": feedback_reason_code or "",
        "feedback_comment": (feedback_comment or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip(),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class DecisionApplicationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def execute_apply(  # noqa: C901, PLR0915
        self,
        *,
        request_id: uuid.UUID | None,
        task_ids: list[int],
        status_id: int,
        comment: str = "",
        expenses: int = 0,
        executor_ids: str | None = None,
        is_private: bool = False,
        dry_run: bool = False,
        confirmed_by_human: bool = False,
        verified_execution_job_id: str | None = None,
        actor: str = "operator",
        operator_user_id: int | None = None,
        service_auth_b64: str,
        # Привязки решений
        decision_id: str | None = None,
        decision_version: int | None = None,
        response_variant_id: str | None = None,
        decision_bindings: list[dict[str, Any]] | None = None,
        feedback_reason_code: str | None = None,
        feedback_comment: str | None = None,
    ) -> dict[str, Any]:
        """
        Главная точка входа для применения решений с гарантией идемпотентности.
        """
        if not task_ids:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Список task_ids не может быть пустым."
            )

        unique_task_ids = sorted(set(task_ids))
        if len(unique_task_ids) != len(task_ids):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "task_ids contains duplicates"
            )

        exec_ids = executor_ids or (
            str(operator_user_id) if operator_user_id else settings.DEFAULT_EXECUTOR_IDS
        )

        # Подготовка канонических привязок решений
        normalized_bindings: dict[int, dict[str, Any]] = {}
        if decision_bindings:
            if decision_id is not None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "conflict_between_single_and_batch_decision_bindings",
                )
            for binding in decision_bindings:
                t_id = int(binding["task_id"])
                if t_id not in unique_task_ids:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        f"binding_task_id_{t_id}_not_in_request_task_ids",
                    )
                normalized_bindings[t_id] = {
                    "task_id": t_id,
                    "decision_id": str(binding["decision_id"]),
                    "decision_version": int(binding["decision_version"]),
                    "response_variant_id": str(binding["response_variant_id"])
                    if binding.get("response_variant_id")
                    else None,
                }
        elif decision_id is not None:
            if len(unique_task_ids) != 1 or decision_version is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "decision_version_required_for_single_task",
                )
            t_id = unique_task_ids[0]
            normalized_bindings[t_id] = {
                "task_id": t_id,
                "decision_id": str(decision_id),
                "decision_version": int(decision_version),
                "response_variant_id": str(response_variant_id)
                if response_variant_id
                else None,
            }

        # -------------------------------------------------------------------
        # 1. ВЕТКА СИМУЛЯЦИИ (dry_run = True)
        # -------------------------------------------------------------------
        if dry_run:
            return await self._execute_simulation(
                task_ids=unique_task_ids,
                status_id=status_id,
                comment=comment,
                expenses=expenses,
                executor_ids=exec_ids,
                is_private=is_private,
                normalized_bindings=normalized_bindings,
                service_auth_b64=service_auth_b64,
                verified_execution_job_id=verified_execution_job_id,
            )

        # -------------------------------------------------------------------
        # 2. РЕАЛЬНОЕ ПРИМЕНЕНИЕ (dry_run = False)
        # -------------------------------------------------------------------
        req_hash = calculate_request_hash(
            task_ids=unique_task_ids,
            status_id=status_id,
            comment=comment,
            expenses=expenses,
            executor_ids=exec_ids,
            is_private=is_private,
            decision_bindings=list(normalized_bindings.values()),
            feedback_reason_code=feedback_reason_code,
            feedback_comment=feedback_comment,
        )

        resolved_request_id = request_id or uuid.uuid4()

        # Проверяем существующий durable request по ID
        existing_request = await self.db.get(
            DecisionApplicationRequest, resolved_request_id
        )
        if existing_request is not None:
            if existing_request.actor != actor:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN, "access_denied_to_request"
                )
            if existing_request.request_hash != req_hash:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "idempotency_conflict_request_hash_mismatch",
                )
            # Возвращаем сохраненный результат существующего запроса
            return await self._build_request_response(existing_request)

        # Проверка активных TicketRuns
        active_runs = list(
            (
                await self.db.scalars(
                    select(TicketRun.task_id).where(
                        TicketRun.task_id.in_(unique_task_ids),
                        TicketRun.completed_at.is_(None),
                    )
                )
            ).all()
        )
        if active_runs:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"tasks_managed_by_active_ticket_runs: {sorted(active_runs)}",
            )

        # Валидация Dead Man's Switch
        try:
            await enforce_triage_apply_rate_limit(
                ticket_count=len(unique_task_ids),
                confirmed_by_human=confirmed_by_human,
            )
        except DeadMansSwitchError as e:
            logger.warning("Dead Man's Switch triggered: %s", e)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(e),
            )

        # Валидация привязок решений и подготовка снимков действий
        decision_by_task: dict[int, DecisionRecord] = {}
        variant_by_task: dict[int, ResponseVariant | None] = {}
        journal = DecisionJournalService(self.db)

        for tid in unique_task_ids:
            binding = normalized_bindings.get(tid)
            if binding:
                dec_uuid = uuid.UUID(binding["decision_id"])
                rec = await journal.require_current(
                    decision_id=dec_uuid,
                    task_id=tid,
                    version=binding["decision_version"],
                )
                # Проверка актуальности заявки в IntraService
                current_task = await intraservice.get_single_task(
                    service_auth_b64, tid
                )
                current_history_payload = await intraservice.get_task_lifetime(
                    service_auth_b64, tid
                )
                current_history = (
                    current_history_payload.get("TaskLifetimes", [])
                    if isinstance(current_history_payload, dict)
                    else (current_history_payload or [])
                )
                expected_fp = (rec.context_json or {}).get("ticket_fingerprint")
                if not current_task or expected_fp != ticket_snapshot_fingerprint(
                    current_task, current_history
                ):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"decision_stale_for_task_{tid}",
                    )
                decision_by_task[tid] = rec

                var_id = binding.get("response_variant_id")
                if var_id:
                    v_rec = await self.db.scalar(
                        select(DecisionResponseVariant).where(
                            DecisionResponseVariant.id == uuid.UUID(var_id),
                            DecisionResponseVariant.decision_id == dec_uuid,
                        )
                    )
                    if v_rec is None:
                        raise HTTPException(
                            status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"variant_{var_id}_not_found_for_task_{tid}",
                        )
                    variant_by_task[tid] = v_rec
                else:
                    variant_by_task[tid] = None
            else:
                # Manual apply: создаем operational-решение
                op_rec = await journal.record_operational(
                    task_id=tid,
                    ticket_run_id=None,
                    action="apply_triage",
                    target={"task_id": tid},
                    parameters={
                        "status_id": status_id,
                        "comment": comment,
                        "expenses": expenses,
                        "executor_ids": exec_ids,
                    },
                    actor=actor,
                )
                decision_by_task[tid] = op_rec
                variant_by_task[tid] = None

        # Создаем durable request и pending attempts в одной транзакции
        durable_request = DecisionApplicationRequest(
            request_id=resolved_request_id,
            actor=actor,
            request_hash=req_hash,
            dry_run=False,
        )
        self.db.add(durable_request)

        attempts_by_task: dict[int, DecisionApplicationAttempt] = {}
        for tid in unique_task_ids:
            is_recommendation = tid in normalized_bindings
            dec_rec = decision_by_task[tid]
            v_rec = variant_by_task.get(tid)

            # Формируем предложенные значения
            envelope = dec_rec.envelope_json or {}
            prop_status = (envelope.get("outcome") or {}).get("target_status_id")
            if v_rec is not None:
                prop_comment = v_rec.response_text
            else:
                prop_comment = (envelope.get("response") or {}).get("text") or ""

            requested_action = {
                "status_id": status_id,
                "comment": comment,
                "expenses": expenses,
                "executor_ids": exec_ids,
                "is_private": is_private,
                "feedback_reason_code": feedback_reason_code,
                "feedback_comment": feedback_comment,
            }
            proposed_action = {
                "status_id": prop_status,
                "comment": prop_comment,
                "variant_id": str(v_rec.id) if v_rec else None,
                "variant_tone": v_rec.tone if v_rec else None,
            }

            attempt = DecisionApplicationAttempt(
                id=uuid.uuid4(),
                request_id=resolved_request_id,
                task_id=tid,
                decision_id=dec_rec.id,
                decision_version=dec_rec.version,
                response_variant_id=v_rec.id if v_rec else None,
                actor=actor,
                source="recommendation_apply"
                if is_recommendation
                else "manual_apply",
                state="pending",
                requested_action_json=sanitize_payload(requested_action),
                proposed_action_json=sanitize_payload(proposed_action),
                suboperations_json={},
            )
            self.db.add(attempt)
            attempts_by_task[tid] = attempt

        try:
            await self.db.commit()
            await self.db.refresh(durable_request)
        except IntegrityError:
            await self.db.rollback()
            # Гонка за request_id: читаем существующий
            existing_request = await self.db.get(
                DecisionApplicationRequest, resolved_request_id
            )
            if existing_request is not None:
                if existing_request.request_hash != req_hash:
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        "idempotency_conflict_request_hash_mismatch",
                    )
                return await self._build_request_response(existing_request)
            raise

        # -------------------------------------------------------------------
        # 3. ВЫПОЛНЕНИЕ ПОПЫТОК (pending -> running -> side effects -> outcome)
        # -------------------------------------------------------------------
        results = []
        redis = get_redis_client()

        for tid in unique_task_ids:
            attempt = attempts_by_task[tid]
            res_item = await self._process_single_attempt(
                attempt=attempt,
                status_id=status_id,
                comment=comment,
                expenses=expenses,
                executor_ids=exec_ids,
                is_private=is_private,
                service_auth_b64=service_auth_b64,
                operator_user_id=operator_user_id,
                verified_execution_job_id=verified_execution_job_id,
                feedback_reason_code=feedback_reason_code,
                feedback_comment=feedback_comment,
            )
            results.append(res_item)

        # Публикация SSE события о применении триажа
        try:
            if redis:
                applied_payload = {
                    "event": "triage_applied",
                    "request_id": str(resolved_request_id),
                    "task_ids": unique_task_ids,
                    "status_id": status_id,
                    "applied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "operator_user_id": operator_user_id,
                }
                await redis.publish(
                    "channel:triage:events", json.dumps(applied_payload)
                )
        except Exception as err:
            logger.warning("Ошибка публикации события triage_applied в Redis: %s", err)

        return {
            "success": any(r.get("update_ok") for r in results),
            "request_id": str(resolved_request_id),
            "results": results,
        }

    async def _process_single_attempt(  # noqa: C901, PLR0915
        self,
        *,
        attempt: DecisionApplicationAttempt,
        status_id: int,
        comment: str,
        expenses: int,
        executor_ids: str,
        is_private: bool,
        service_auth_b64: str,
        operator_user_id: int | None,
        verified_execution_job_id: str | None,
        feedback_reason_code: str | None,
        feedback_comment: str | None,
    ) -> dict[str, Any]:
        """Обрабатывает одну попытку применения с переходом состояний и атомарной фиксацией."""
        tid = attempt.task_id
        now = dt.datetime.now(dt.timezone.utc)

        # Переход pending -> running с захватом claim token
        claim_token = str(uuid.uuid4())
        lease_duration = dt.timedelta(seconds=60)
        attempt.state = "running"
        attempt.claim_token = claim_token
        attempt.lease_expires_at = now + lease_duration
        attempt.version += 1
        await self.db.commit()

        # 1. Проверка условий финализации для статуса 29
        if status_id == 29:
            from app.services.rules.credentials import CredentialsRule
            from app.services.triage_service import TriageService

            task_snapshot = await intraservice.get_single_task(
                service_auth_b64, tid
            )
            execution_decision = (
                CredentialsRule().evaluate(task_snapshot)
                if task_snapshot
                else None
            )
            rule_decision = (
                execution_decision.to_dict() if execution_decision else {}
            )
            rule_type = rule_decision.get("rule_type")
            expected_actions = TriageService._EXECUTION_RULE_ACTIONS.get(
                rule_type
            )
            if expected_actions:
                proof_ok, proof_error = (
                    await TriageService._validate_execution_proof(
                        verified_execution_job_id,
                        tid,
                        expected_actions,
                        db=self.db,
                    )
                )
                if not proof_ok:
                    return await self._finalize_attempt(
                        attempt=attempt,
                        state="failed",
                        suboperations={
                            "gate_proof": {
                                "outcome": "failed",
                                "error": proof_error,
                            }
                        },
                        error_code="execution_proof_required",
                        error_message=proof_error,
                    )

        # 2. Промежуточный статус 27 (если целевой != 27)
        interm_subop = {"outcome": "not_requested"}
        if status_id != 27:
            in_work_res = await intraservice.update_task_full_structured(
                auth_b64=service_auth_b64,
                task_id=tid,
                status_id=27,
                executor_ids=executor_ids,
            )
            interm_subop = (
                in_work_res.to_dict()
                if hasattr(in_work_res, "to_dict")
                else dict(in_work_res)
            )
            in_work_confirmed = (
                in_work_res.is_confirmed
                if hasattr(in_work_res, "is_confirmed")
                else (interm_subop.get("outcome") == "confirmed")
            )
            if not in_work_confirmed:
                outcome_val = (
                    in_work_res.outcome.value
                    if hasattr(in_work_res, "outcome")
                    and hasattr(in_work_res.outcome, "value")
                    else interm_subop.get("outcome")
                )
                state = "unknown" if outcome_val == "unknown" else "failed"
                err_code = (
                    getattr(in_work_res, "error_code", None)
                    or interm_subop.get("error_code")
                    or "intermediate_status_failed"
                )
                err_msg = (
                    getattr(in_work_res, "error_message", None)
                    or interm_subop.get("error_message")
                    or "Не удалось перевести в статус «В работе»."
                )
                return await self._finalize_attempt(
                    attempt=attempt,
                    state=state,
                    suboperations={"intermediate_status": interm_subop},
                    error_code=err_code,
                    error_message=err_msg,
                )

        # 3. Целевое обновление статуса/комментария
        target_res = await intraservice.update_task_full_structured(
            auth_b64=service_auth_b64,
            task_id=tid,
            status_id=status_id,
            comment=comment if comment else None,
            executor_ids=executor_ids,
            is_private=is_private,
        )
        target_subop = (
            target_res.to_dict()
            if hasattr(target_res, "to_dict")
            else dict(target_res)
        )
        target_confirmed = (
            target_res.is_confirmed
            if hasattr(target_res, "is_confirmed")
            else (target_subop.get("outcome") == "confirmed")
        )

        if not target_confirmed:
            outcome_val = (
                target_res.outcome.value
                if hasattr(target_res, "outcome")
                and hasattr(target_res.outcome, "value")
                else target_subop.get("outcome")
            )
            state = "unknown" if outcome_val == "unknown" else "failed"
            err_code = (
                getattr(target_res, "error_code", None)
                or target_subop.get("error_code")
                or "target_update_failed"
            )
            err_msg = (
                getattr(target_res, "error_message", None)
                or target_subop.get("error_message")
                or "Ошибка обновления статуса/комментария заявки."
            )
            return await self._finalize_attempt(
                attempt=attempt,
                state=state,
                suboperations={
                    "intermediate_status": interm_subop,
                    "target_update": target_subop,
                },
                error_code=err_code,
                error_message=err_msg,
            )

        # 4. Списание трудозатрат (только после подтвержденного целевого обновления!)
        expenses_subop = {"outcome": "not_requested"}
        exp_confirmed = True
        if expenses and expenses > 0:
            exp_res = await intraservice.add_task_expenses_structured(
                auth_b64=service_auth_b64,
                task_id=tid,
                minutes=expenses,
                user_id=operator_user_id,
            )
            expenses_subop = (
                exp_res.to_dict()
                if hasattr(exp_res, "to_dict")
                else dict(exp_res)
            )
            exp_confirmed = (
                exp_res.is_confirmed
                if hasattr(exp_res, "is_confirmed")
                else (expenses_subop.get("outcome") == "confirmed")
            )

        # 5. Автоиндексация RAG
        clean_comment = comment.strip() if comment else ""
        should_index = False
        if status_id == 29 and clean_comment:
            should_index = True
        elif status_id == 30 and len(clean_comment) >= 35:
            if not clean_comment.startswith("Заявка переведена в статус Отменена"):
                should_index = True

        if should_index:
            try:
                from app.services.rag import index_task_knowledge

                task_data = await intraservice.get_single_task(
                    service_auth_b64, tid
                )
                if task_data:
                    t_name = task_data.get("Name") or f"Заявка #{tid}"
                    t_desc = task_data.get("Description") or ""
                    s_id = task_data.get("ServiceId") or 0
                    s_name = task_data.get("ServiceName") or "Общие"
                    st_name = "Выполнена" if status_id == 29 else "Отменена"
                    await index_task_knowledge(
                        db=self.db,
                        task_id=tid,
                        original_name=t_name,
                        problem=f"{t_name}. {t_desc}".strip(),
                        solution=clean_comment,
                        service_id=s_id,
                        service_name=s_name,
                        status_name=st_name,
                        classification_data={
                            "type": "auto_indexed_by_triage",
                            "status_id": status_id,
                        },
                    )
            except Exception as e:
                logger.error("Ошибка автоиндексации заявки #%d в RAG: %s", tid, e)

        # Итоговое состояние попытки
        if exp_confirmed:
            final_state = "succeeded"
        else:
            final_state = "partial_failure"

        all_subops = {
            "intermediate_status": interm_subop,
            "target_update": target_subop,
            "expenses": expenses_subop,
        }

        return await self._finalize_attempt(
            attempt=attempt,
            state=final_state,
            suboperations=all_subops,
            applied_status_id=status_id,
            applied_comment=comment,
            applied_expenses=expenses if exp_confirmed else 0,
            feedback_reason_code=feedback_reason_code,
            feedback_comment=feedback_comment,
        )

    async def _finalize_attempt(  # noqa: C901, PLR0915
        self,
        *,
        attempt: DecisionApplicationAttempt,
        state: str,
        suboperations: dict[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
        applied_status_id: int | None = None,
        applied_comment: str | None = None,
        applied_expenses: int | None = None,
        feedback_reason_code: str | None = None,
        feedback_comment: str | None = None,
    ) -> dict[str, Any]:
        """Атомарно фиксирует завершение попытки, создает DecisionApplication и DecisionFeedback."""
        now = dt.datetime.now(dt.timezone.utc)
        tid = attempt.task_id

        attempt.state = state
        attempt.claim_token = None
        attempt.lease_expires_at = None
        attempt.suboperations_json = sanitize_payload(suboperations)
        attempt.completed_at = now
        attempt.version += 1

        # Определение статуса проекции DecisionApplication
        is_manual = attempt.source == "manual_apply"
        prop = attempt.proposed_action_json or {}
        prop_status = prop.get("status_id")
        prop_comment = prop.get("comment")

        if state == "succeeded":
            if is_manual:
                app_state = "applied_unmodified"
            else:
                status_same = prop_status == applied_status_id
                comment_same = (
                    (prop_comment or "").strip()
                    == (applied_comment or "").strip()
                )
                app_state = (
                    "applied_unmodified"
                    if (status_same and comment_same)
                    else "applied_modified"
                )
        elif state in {"partial_failure", "unknown"}:
            app_state = "needs_review"
        else:
            app_state = "failed"

        applied_action = {
            "status_id": applied_status_id,
            "comment": applied_comment,
            "expenses": applied_expenses,
        }
        verified_result = {
            "outcome": state,
            "error_code": error_code,
            "error_message": error_message,
            "suboperations": suboperations,
        }

        # Создаем или обновляем DecisionApplication
        if attempt.decision_id:
            application = await self.db.scalar(
                select(DecisionApplication).where(
                    DecisionApplication.attempt_id == attempt.id
                )
            )
            if application is None:
                application = DecisionApplication(
                    id=uuid.uuid4(),
                    decision_id=attempt.decision_id,
                    command_id=None,
                    attempt_id=attempt.id,
                    task_id=tid,
                    application_type="triage_apply",
                    state=app_state,
                    proposed_action_json=sanitize_payload(attempt.proposed_action_json or {}),
                    applied_action_json=sanitize_payload(applied_action),
                    verified_result_json=sanitize_payload(verified_result),
                    operator=attempt.actor or "unknown",
                    created_at=now,
                )
                self.db.add(application)
            else:
                application.state = app_state
                application.applied_action_json = sanitize_payload(applied_action)
                application.verified_result_json = sanitize_payload(verified_result)

        # Для recommendation_apply и сопоставимого результата создаем DecisionFeedback
        if attempt.source == "recommendation_apply" and state in {
            "succeeded",
            "partial_failure",
        }:
            status_changed = prop_status != applied_status_id
            comment_diff = calculate_comment_diff(prop_comment, applied_comment)
            comment_changed = not comment_diff.get("identical", True)

            verdict = (
                "accepted"
                if (not status_changed and not comment_changed)
                else "modified"
            )

            # Определение оснований вердикта согласно плану:
            # - accepted: reason_code=None, reason_source="none"
            # - modified:
            #   - оператор передал причину: reason_code=feedback_reason_code, reason_source="operator"
            #   - иначе: status_changed -> wrong_status, comment_changed -> edited_comment
            if verdict == "accepted":
                calc_reason = None
                calc_source = "none"
            elif feedback_reason_code:
                calc_reason = feedback_reason_code
                calc_source = "operator"
            elif status_changed:
                calc_reason = "wrong_status"
                calc_source = "inferred"
            elif comment_changed:
                calc_reason = "edited_comment"
                calc_source = "inferred"
            else:
                calc_reason = "other"
                calc_source = "inferred"

            feedback_final_action = {
                "applied_status_id": applied_status_id,
                "applied_comment": applied_comment,
                "proposed_status_id": prop_status,
                "proposed_comment": prop_comment,
                "status_changed": status_changed,
                "comment_changed": comment_changed,
                "comment_diff": comment_diff,
                "response_variant_id": prop.get("variant_id"),
                "variant_tone": prop.get("variant_tone"),
            }

            journal = DecisionJournalService(self.db)
            await journal.add_feedback(
                decision_id=attempt.decision_id,
                verdict=verdict,
                reason_code=calc_reason,
                reason_source=calc_source,
                operator_reason_code=feedback_reason_code,
                comment=feedback_comment,
                final_action=feedback_final_action,
                actor=attempt.actor,
                commit=False,
                attempt_id=attempt.id,
                event_id=None,
                source="recommendation_apply",
                task_id=tid,
                response_variant_id=prop.get("variant_id"),
            )

        await self.db.commit()

        update_ok = state in {"succeeded", "partial_failure"}
        expenses_ok = state == "succeeded"

        return {
            "task_id": tid,
            "attempt_id": str(attempt.id),
            "status": state,
            "update_ok": update_ok,
            "expenses_ok": expenses_ok,
            "error": error_message,
            "suboperations": suboperations,
        }

    async def _execute_simulation(
        self,
        *,
        task_ids: list[int],
        status_id: int,
        comment: str,
        expenses: int,
        executor_ids: str,
        is_private: bool,
        normalized_bindings: dict[int, dict[str, Any]],
        service_auth_b64: str,
        verified_execution_job_id: str | None,
    ) -> dict[str, Any]:
        """Выполняет чистую симуляцию dry_run без побочных эффектов и записи в БД."""
        results = []
        for tid in task_ids:
            binding = normalized_bindings.get(tid)
            is_valid = True
            err_msg = None
            if binding:
                rec = await self.db.get(
                    DecisionRecord, uuid.UUID(binding["decision_id"])
                )
                if rec is None or rec.task_id != tid:
                    is_valid = False
                    err_msg = "decision_not_found"
                elif rec.version != binding["decision_version"]:
                    is_valid = False
                    err_msg = "decision_version_mismatch"

            results.append(
                {
                    "task_id": tid,
                    "status": "simulated",
                    "target_status_id": status_id,
                    "update_ok": is_valid,
                    "expenses_ok": is_valid,
                    "error": err_msg,
                    "simulation": True,
                }
            )
        all_ok = all(r["update_ok"] for r in results) if results else True
        return {
            "simulated": True,
            "dry_run": True,
            "status": "simulated",
            "overall_outcome": "confirmed" if all_ok else "failed",
            "results": results,
        }

    async def _build_request_response(
        self, req: DecisionApplicationRequest
    ) -> dict[str, Any]:
        """Формирует ответ по существующему сохраненному запросу."""
        attempts = list(
            (
                await self.db.scalars(
                    select(DecisionApplicationAttempt).where(
                        DecisionApplicationAttempt.request_id == req.id
                    )
                )
            ).all()
        )
        results = []
        for att in attempts:
            update_ok = att.state in {"succeeded", "partial_failure"}
            expenses_ok = att.state == "succeeded"
            err = (
                (att.suboperations_json or {})
                .get("target_update", {})
                .get("error_message")
            )
            results.append(
                {
                    "task_id": att.task_id,
                    "attempt_id": str(att.id),
                    "status": att.state,
                    "update_ok": update_ok,
                    "expenses_ok": expenses_ok,
                    "error": err,
                    "suboperations": att.suboperations_json,
                }
            )
        return {
            "success": any(r.get("update_ok") for r in results),
            "request_id": str(req.id),
            "results": results,
        }

    async def reconcile_attempt(
        self,
        *,
        attempt_id: uuid.UUID | str,
        expected_version: int | None = None,
        actor: str = "system",
        service_auth_b64: str,
    ) -> dict[str, Any]:
        """
        Идемпотентная сверка попытки в состоянии unknown с IntraService.
        Выполняет ТОЛЬКО read-only запросы к IntraService.
        """
        att_uuid = uuid.UUID(str(attempt_id))
        attempt = await self.db.get(DecisionApplicationAttempt, att_uuid)
        if attempt is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "attempt_not_found")

        if expected_version is not None and attempt.version != expected_version:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "attempt_version_conflict"
            )

        if attempt.state != "unknown":
            return {
                "attempt_id": str(attempt.id),
                "state": attempt.state,
                "reconciled": False,
                "reason": "attempt_is_not_unknown",
                "version": attempt.version,
            }

        tid = attempt.task_id
        # Читаем актуальное состояние из IntraService
        task_data = await intraservice.get_single_task(service_auth_b64, tid)
        history_payload = await intraservice.get_task_lifetime(
            service_auth_b64, tid
        )
        history = (
            history_payload.get("TaskLifetimes", [])
            if isinstance(history_payload, dict)
            else (history_payload or [])
        )

        req_act = attempt.requested_action_json or {}
        target_status = req_act.get("status_id")
        req_comment = (req_act.get("comment") or "").strip()

        # Проверяем, применился ли статус и комментарий в истории
        current_status = task_data.get("StatusId") if task_data else None
        comment_found = False
        for item in history:
            text = str(
                item.get("Comments")
                or item.get("Comment")
                or item.get("Text")
                or ""
            ).strip()
            if req_comment and req_comment in text:
                comment_found = True
                break

        status_matched = current_status == target_status
        now = dt.datetime.now(dt.timezone.utc)

        reconciled_state = "unknown"
        if status_matched and (not req_comment or comment_found):
            reconciled_state = "succeeded"
        elif not status_matched and not comment_found:
            reconciled_state = "failed"

        if reconciled_state != "unknown":
            attempt.state = reconciled_state
            attempt.completed_at = now
            attempt.version += 1
            subops = attempt.suboperations_json or {}
            subops["reconciliation"] = {
                "reconciled_at": now.isoformat(),
                "reconciled_by": actor,
                "status_matched": status_matched,
                "comment_found": comment_found,
                "outcome": reconciled_state,
            }
            attempt.suboperations_json = sanitize_payload(subops)

            # Обновляем связанный DecisionApplication
            application = await self.db.scalar(
                select(DecisionApplication).where(
                    DecisionApplication.attempt_id == attempt.id
                )
            )
            if application:
                application.status = (
                    "applied_unmodified"
                    if reconciled_state == "succeeded"
                    else "failed"
                )
                ver_res = application.verified_result_json or {}
                ver_res["reconciliation"] = subops["reconciliation"]
                ver_res["outcome"] = reconciled_state
                application.verified_result_json = sanitize_payload(ver_res)

            await self.db.commit()

        return {
            "attempt_id": str(attempt.id),
            "state": attempt.state,
            "reconciled": reconciled_state != "unknown",
            "outcome": "confirmed" if reconciled_state == "succeeded" else reconciled_state,
            "version": attempt.version,
            "status_matched": status_matched,
            "comment_found": comment_found,
        }
