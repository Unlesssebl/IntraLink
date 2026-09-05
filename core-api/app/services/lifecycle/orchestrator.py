"""
Оркестратор автономного жизненного цикла заявок (Autonomous Ticket Orchestrator).
Выполняет периодический обход заявок, назначенных на системный аккаунт бота,
и проводит их по детерминированному конечному автомату (FSM).
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, Optional

from app.config import settings
from app.database.db import AsyncSessionLocal, CommandRecord
from app.services.command_service import CommandService
from app.services.intraservice import (
    add_task_comment,
    add_task_expenses,
    get_task_comments,
    get_tasks,
    update_task_custom_fields,
    update_task_status,
)
from app.services.lifecycle.intent_analyzer import IntentAnalyzer
from app.services.lifecycle.models import (
    IntentAnalysisResult,
    LifecycleStepResult,
    UserReplyIntent,
)
from app.services.lifecycle.state_machine import LifecycleStateMachine
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.services.lifecycle.orchestrator")

class AutonomousTicketOrchestrator:
    """Сервис управления автономным прохождением жизненного цикла заявок."""

    def __init__(self):
        self._is_active = True

    async def process_assigned_tasks(self, service_auth_b64: str) -> list[LifecycleStepResult]:
        """
        Главный цикл сканирования и продвижения назначенных на бота задач по FSM.
        Вызывается из цикла Poller Service под защитой Leader Lock.
        """
        if not settings.AUTONOMOUS_LIFECYCLE_ENABLED:
            logger.debug("Автономный жизненный цикл отключен в конфигурации (AUTONOMOUS_LIFECYCLE_ENABLED=False).")
            return []

        bot_user_id = settings.INTRASERVICE_SERVICE_USER_ID
        if not bot_user_id:
            logger.debug("INTRASERVICE_SERVICE_USER_ID не задан. Автономный цикл пропущен.")
            return []

        redis = get_redis_client()
        results: list[LifecycleStepResult] = []

        # 1. Запрашиваем активные задачи, назначенные на бота (31=Открыта, 35=Требует уточнения, 27=В работе)
        status_ids_filter = f"{settings.STATUS_OPEN_ID},{settings.STATUS_WAITING_ID},{settings.STATUS_IN_PROGRESS_ID}"
        try:
            tasks_resp = await get_tasks(
                service_auth_b64,
                {
                    "ExecutorId": bot_user_id,
                    "StatusId": status_ids_filter,
                    "include": "executorids,status,customfields",
                },
            )
        except Exception as exc:
            logger.error("Ошибка получения активных задач бота из IntraService: %s", exc)
            return []

        tasks: list[dict[str, Any]] = []
        if isinstance(tasks_resp, dict):
            tasks = tasks_resp.get("Tasks", [])
        elif isinstance(tasks_resp, list):
            tasks = tasks_resp

        if not tasks:
            return []

        # Строгая фильтрация в Python: IntraService API GET /api/task игнорирует параметр ExecutorId в query!
        bot_tasks = []
        for t in tasks:
            t_exec_id = t.get("ExecutorId")
            t_exec_ids_raw = str(t.get("ExecutorIds") or "")
            t_exec_ids = [int(x.strip()) for x in t_exec_ids_raw.split(",") if x.strip().isdigit()]
            if t_exec_id == bot_user_id or bot_user_id in t_exec_ids:
                bot_tasks.append(t)

        if not bot_tasks:
            return []

        logger.info("🤖 Найдено %d активных задач, действительно назначенных на бота svc_intralink (ID: %s)", len(bot_tasks), bot_user_id)

        for task in bot_tasks:
            task_id = int(task.get("Id") or 0)
            if not task_id:
                continue

            # Защита от гонок: атомарный lease-замок на задачу в Redis
            lock_key = f"lock:lifecycle:task:{task_id}"
            acquired = await redis.set(
                lock_key, "processing", nx=True, ex=settings.AUTONOMOUS_TASK_LEASE_TTL
            )
            if not acquired:
                logger.debug("Задача #%d уже обрабатывается другим циклом. Пропуск.", task_id)
                continue

            try:
                step_res = await self._process_single_task(service_auth_b64, task, redis)
                if step_res:
                    results.append(step_res)
            except Exception as e:
                logger.exception("Непредвиденная ошибка обработки жизненного цикла для задачи #%d: %s", task_id, e)
            finally:
                # Освобождаем блокировку после завершения шага
                try:
                    await redis.delete(lock_key)
                except Exception:
                    pass

        return results

    async def _process_single_task(
        self, service_auth_b64: str, task: dict[str, Any], redis
    ) -> Optional[LifecycleStepResult]:
        """Обработка отдельной задачи по шагам конечного автомата."""
        task_id = int(task.get("Id") or 0)
        status_id = int(task.get("StatusId") or 0)

        # Железный Guard: задача должна быть назначена на бота!
        bot_user_id = settings.INTRASERVICE_SERVICE_USER_ID
        t_exec_id = task.get("ExecutorId")
        t_exec_ids_raw = str(task.get("ExecutorIds") or "")
        t_exec_ids = [int(x.strip()) for x in t_exec_ids_raw.split(",") if x.strip().isdigit()]
        if t_exec_id != bot_user_id and bot_user_id not in t_exec_ids:
            logger.debug("Задача #%d не назначена на бота svc_intralink (ExecutorId: %s). Пропуск.", task_id, t_exec_id)
            return None

        # -------------------------------------------------------------
        # ВЕТКА 1: Статус 31 (Открыта) -> Проверка реквизитов / Запуск
        # -------------------------------------------------------------
        if status_id == settings.STATUS_OPEN_ID:
            step = LifecycleStateMachine.evaluate_open_task(task)

            # Не хватает реквизитов -> перевод в статус 35 (Требует уточнения)
            if step.action_taken == "request_clarification":
                st_ok = await update_task_status(service_auth_b64, task_id, settings.STATUS_WAITING_ID)
                comm_ok = False
                if st_ok and step.comment:
                    comm_ok = await add_task_comment(service_auth_b64, task_id, step.comment)
                logger.info("Задача #%d переведена в 'Требует уточнения' (status_ok=%s, comm_ok=%s)", task_id, st_ok, comm_ok)
                return step

            # Все реквизиты есть -> перевод в 27 (В работе) + запуск WinRM команды
            if step.action_taken == "dispatch_execution":
                pc_name, printer_addr = self._extract_task_target_params(task)
                job_id = await self._dispatch_execution_command(
                    task_id=task_id,
                    pc_name=pc_name,
                    printer_address=printer_addr,
                    redis=redis,
                )
                step.dispatched_command_id = job_id

                # Переводим задачу в статус "В работе"
                st_ok = await update_task_status(service_auth_b64, task_id, settings.STATUS_IN_PROGRESS_ID)
                if st_ok and step.comment:
                    await add_task_comment(service_auth_b64, task_id, step.comment)

                # Запоминаем ID активного задания в Redis
                if job_id:
                    await redis.set(f"task:{task_id}:execution_job", job_id, ex=86400)
                logger.info("Задача #%d переведена в 'В работе' и поставлена в Command Bus (job=%s)", task_id, job_id)
                return step

            return step

        # -------------------------------------------------------------
        # ВЕТКА 2: Статус 35 (Требует уточнения) -> Анализ ответов
        # -------------------------------------------------------------
        if status_id == settings.STATUS_WAITING_ID:
            comments = await get_task_comments(service_auth_b64, task_id)
            latest_applicant_comment = self._find_latest_applicant_comment(task, comments)
            if not latest_applicant_comment:
                return None

            comm_val = latest_applicant_comment.get("Comments") or latest_applicant_comment.get("Comment", "")
            comment_id = str(latest_applicant_comment.get("Id") or hash(comm_val))
            seen_key = f"task:{task_id}:processed_comment:{comment_id}"
            if await redis.get(seen_key):
                return None  # Этот ответ уже был обработан

            comment_text = str(comm_val)
            intent_res = await IntentAnalyzer.analyze_with_llm(comment_text, task)

            # Защита от бесконечного пинг-понга: счетчик попыток уточнения
            attempt_key = f"task:{task_id}:clarification_attempts"
            attempts = int(await redis.incr(attempt_key))
            await redis.expire(attempt_key, 86400 * 7)

            if attempts > 2 and intent_res.intent not in (UserReplyIntent.PROVIDE_DATA, UserReplyIntent.CANCEL_REQUEST):
                await update_task_status(service_auth_b64, task_id, settings.STATUS_OPEN_ID)
                await add_task_comment(
                    service_auth_b64,
                    task_id,
                    "Заявитель не предоставил сетевые реквизиты после повторного запроса. "
                    "Заявка передана на ручное сопровождение дежурному инженеру 1-й линии."
                )
                await redis.set(seen_key, "1", ex=86400 * 30)
                logger.info("Задача #%d: превышен лимит попыток уточнения (%d). Эскалация человеку.", task_id, attempts)
                return LifecycleStepResult(
                    task_id=task_id,
                    action_taken="max_clarifications_exceeded",
                    previous_status_id=settings.STATUS_WAITING_ID,
                    target_status_id=settings.STATUS_OPEN_ID,
                    escalated_to_human=True,
                )

            step = LifecycleStateMachine.evaluate_waiting_task(task, intent_res)

            # Возобновление в работу (31)
            if step.action_taken == "resume_to_open":
                fields_to_update = []
                if intent_res.extracted_ip:
                    fields_to_update.append({"FieldId": settings.PRINTER_IP_CUSTOM_FIELD_ID, "Value": intent_res.extracted_ip})
                if intent_res.extracted_pc:
                    fields_to_update.append({"FieldId": settings.PRINTER_PC_CUSTOM_FIELD_ID, "Value": intent_res.extracted_pc})

                if fields_to_update:
                    await update_task_custom_fields(service_auth_b64, task_id, fields_to_update)

                await update_task_status(service_auth_b64, task_id, settings.STATUS_OPEN_ID)
                if step.comment:
                    await add_task_comment(service_auth_b64, task_id, step.comment)
                await redis.set(seen_key, "1", ex=86400 * 30)
                logger.info("Задача #%d возобновлена в 'Открыта' после ответа заявителя", task_id)
                return step

            # Заявитель попросил закрыть/отменить (30)
            if step.action_taken == "cancel_by_user":
                await update_task_status(service_auth_b64, task_id, settings.STATUS_CANCELLED_ID)
                if step.comment:
                    await add_task_comment(service_auth_b64, task_id, step.comment)
                await redis.set(seen_key, "1", ex=86400 * 30)
                logger.info("Задача #%d отменена по запросу заявителя", task_id)
                return step

            # Ответ на встречный вопрос
            if step.action_taken == "reply_clarification":
                if step.comment:
                    await add_task_comment(service_auth_b64, task_id, step.comment)
                await redis.set(seen_key, "1", ex=86400 * 30)
                return step

            # Эскалация на живого инженера
            if step.action_taken == "escalate_to_human":
                await update_task_status(service_auth_b64, task_id, settings.STATUS_OPEN_ID)
                if step.comment:
                    await add_task_comment(service_auth_b64, task_id, step.comment)
                await redis.set(seen_key, "1", ex=86400 * 30)
                logger.info("Задача #%d эскалирована на человека из-за нестандартного ответа", task_id)
                return step

            return step

        # -------------------------------------------------------------
        # ВЕТКА 3: Статус 27 (В работе) -> Проверка завершения задачи
        # -------------------------------------------------------------
        if status_id == settings.STATUS_IN_PROGRESS_ID:
            raw_job_id = await redis.get(f"task:{task_id}:execution_job")
            if not raw_job_id:
                return None

            job_id = raw_job_id.decode() if isinstance(raw_job_id, bytes) else str(raw_job_id)
            job_data = None
            try:
                command_uuid = uuid.UUID(job_id)
                async with AsyncSessionLocal() as db:
                    command = await db.get(CommandRecord, command_uuid)
                    if command:
                        result = dict(command.result_json or {})
                        job_data = {
                            **result,
                            "status": command.status,
                            "created_at": command.created_at.timestamp(),
                            "message": command.error_message or result.get("message"),
                        }
            except (ValueError, TypeError):
                pass
            if job_data is None:
                raw_job_data = await redis.get(f"execution_job:{job_id}")
                if not raw_job_data:
                    return None
                job_data = json.loads(raw_job_data.decode() if isinstance(raw_job_data, bytes) else raw_job_data)
            job_status = job_data.get("status")
            if job_status == "succeeded":
                job_status = "success"
            elif job_status == "needs_review":
                job_status = "failed"

            # FSM Guard: защита от конфликта с оператором (ручной перехват)
            current_status = int(task.get("StatusId") or 0)
            if current_status != settings.STATUS_IN_PROGRESS_ID:
                logger.info("Задача #%d: статус был изменен оператором на %d. Автономная финализация пропущена.", task_id, current_status)
                await redis.delete(f"task:{task_id}:execution_job")
                return None

            # Проверка таймаута зависших заданий (Zombie Jobs)
            created_at = float(job_data.get("created_at") or 0)
            now_ts = time.time()
            if job_status == "awaiting_approval":
                return None
            if job_status in ("queued", "running"):
                if created_at > 0 and (now_ts - created_at) > 600:
                    logger.warning("Задача #%d: задание %s зависло в '%s' (> 10 мин). Таймаут и эскалация.", task_id, job_id, job_status)
                    await update_task_status(service_auth_b64, task_id, settings.STATUS_OPEN_ID)
                    await add_task_comment(
                        service_auth_b64,
                        task_id,
                        "Автоматическая установка приостановлена: превышен таймаут отклика Execution Worker (10 минут). "
                        "Заявка передана на ручное исполнение дежурному инженеру 2-й линии."
                    )
                    await redis.delete(f"task:{task_id}:execution_job")
                    return LifecycleStepResult(
                        task_id=task_id,
                        action_taken="execution_timeout_escalated",
                        previous_status_id=settings.STATUS_IN_PROGRESS_ID,
                        target_status_id=settings.STATUS_OPEN_ID,
                        escalated_to_human=True,
                        error="Execution worker timeout (> 10 min)",
                    )
                return None

            # Если задача завершена успешно
            if job_status == "success":
                # Защита от повторного списания трудозатрат: если задача уже закрыта
                if current_status == settings.STATUS_COMPLETED_ID or job_data.get("ticket_close_ok"):
                    await redis.delete(f"task:{task_id}:execution_job")
                    return LifecycleStepResult(
                        task_id=task_id,
                        action_taken="already_closed_by_worker",
                        previous_status_id=settings.STATUS_IN_PROGRESS_ID,
                        target_status_id=settings.STATUS_COMPLETED_ID,
                        success=True,
                    )

                step = LifecycleStateMachine.evaluate_execution_result(task, is_success=True)
                await update_task_status(service_auth_b64, task_id, settings.STATUS_COMPLETED_ID)
                if step.comment:
                    await add_task_comment(service_auth_b64, task_id, step.comment)
                if step.expenses:
                    await add_task_expenses(service_auth_b64, task_id, step.expenses, user_id=settings.INTRASERVICE_SERVICE_USER_ID)
                await redis.delete(f"task:{task_id}:execution_job")
                logger.info("Задача #%d успешно финализирована в статус 'Выполнена' (29)", task_id)
                return step

            # Если задача завершилась ошибкой
            if job_status == "failed":
                err_msg = job_data.get("message") or "Технический сбой выполнения"
                step = LifecycleStateMachine.evaluate_execution_result(task, is_success=False, error_message=err_msg)

                # Офлайн ПК -> перевод в 35 (Требует уточнения)
                if step.target_status_id == settings.STATUS_WAITING_ID:
                    await update_task_status(service_auth_b64, task_id, settings.STATUS_WAITING_ID)
                    if step.comment:
                        await add_task_comment(service_auth_b64, task_id, step.comment)
                    await redis.delete(f"task:{task_id}:execution_job")
                    logger.info("Задача #%d: ПК офлайн. Переведена в 'Требует уточнения'", task_id)
                    return step

                # Внутренний сбой -> эскалация человеку в статус 31
                await update_task_status(service_auth_b64, task_id, settings.STATUS_OPEN_ID)
                if step.comment:
                    await add_task_comment(service_auth_b64, task_id, step.comment)
                await redis.delete(f"task:{task_id}:execution_job")
                logger.info("Задача #%d: сбой исполнения. Возвращена в 'Открыта' и эскалирована", task_id)
                return step

        return None

    def _extract_task_target_params(self, task: dict[str, Any]) -> tuple[str, str]:
        """Извлекает имя ПК и IP принтера из кастомных полей либо описания заявки."""
        pc_name = ""
        printer_addr = ""

        # Проверяем кастомные поля
        for cf in task.get("CustomFields", []):
            fid = cf.get("CustomFieldId") or cf.get("FieldId")
            val = str(cf.get("Value") or "").strip()
            if fid == settings.PRINTER_PC_CUSTOM_FIELD_ID and val:
                pc_name = val
            elif fid == settings.PRINTER_IP_CUSTOM_FIELD_ID and val:
                printer_addr = val

        # Если не найдены в кастомных полях, ищем в тексте через IntentAnalyzer regex
        if not pc_name or not printer_addr:
            text_corpus = f"{task.get('Name', '')} {task.get('Description', '')}"
            regex_res = IntentAnalyzer.analyze_fast_regex(text_corpus)
            if regex_res:
                if not pc_name and regex_res.extracted_pc:
                    pc_name = regex_res.extracted_pc
                if not printer_addr and regex_res.extracted_ip:
                    printer_addr = regex_res.extracted_ip

        return pc_name, printer_addr

    def _find_latest_applicant_comment(
        self, task: dict[str, Any], comments: list[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        """Находит самый свежий содержательный комментарий от заявителя."""
        if not comments:
            return None

        bot_user_id = settings.INTRASERVICE_SERVICE_USER_ID
        creator_id = task.get("CreatorId")

        # Комментарии обычно идут по возрастанию или убыванию даты
        sorted_comments = sorted(
            comments,
            key=lambda c: str(c.get("Date") or c.get("Created") or ""),
            reverse=True,
        )

        for comm in sorted_comments:
            editor_id = comm.get("EditorId") or comm.get("UserId")
            # Пропускаем комментарии бота и системные комментарии
            if editor_id == bot_user_id:
                continue
            text = str(comm.get("Comments") or comm.get("Comment") or "").strip()
            if not text or len(text) < 2:
                continue
            return comm

        return None

    async def _dispatch_execution_command(
        self, task_id: int, pc_name: str, printer_address: str, redis
    ) -> str:
        """Create a durable v2 command; policy keeps printer changes awaiting approval."""
        target_payload = {
            "task_id": task_id,
            "pc_name": pc_name,
            "printer_address": printer_address,
        }
        params_payload = {
            "task_id": task_id,
            "pc_name": pc_name,
            "printer_name": printer_address,
            "target": pc_name,
        }

        async with AsyncSessionLocal() as db:
            command, _ = await CommandService(db).create(
                action="install_printer",
                target=target_payload,
                parameters=params_payload,
                idempotency_key=(
                    f"lifecycle:install_printer:{task_id}:"
                    f"{hashlib.sha256(f'{pc_name}|{printer_address}'.encode()).hexdigest()[:16]}"
                ),
                initiator="autonomous_orchestrator",
                source="poller",
                priority=7,
            )
            return str(command.id)


_orchestrator_instance: Optional[AutonomousTicketOrchestrator] = None


def get_ticket_orchestrator() -> AutonomousTicketOrchestrator:
    """Возвращает синглтон автономного оркестратора."""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = AutonomousTicketOrchestrator()
    return _orchestrator_instance
