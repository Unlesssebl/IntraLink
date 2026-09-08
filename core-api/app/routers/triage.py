"""
Роутер централизованного триажа и пакетной обработки очередей для Web UI и Helpdesk Agent.
Спроектирован как тонкий контроллер (SRP), делегирующий логику в TriageService и TriageSessionManager.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Literal
import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import (
    AsyncSessionLocal,
    CommandRecord,
    DecisionRecord,
    DecisionStep,
    TicketRun,
    get_db,
)
from app.routers.deps import (
    get_service_auth_b64,
    principal_subject,
    require_permission,
    verify_trusted_origin,
)
from app.services import intraservice
from app.services.rag import (
    index_task_knowledge,
    search_knowledge_base,
    sync_historical_closed_tasks,
)
from app.services.rules.catalog import ROOT_SERVICES
from app.services.safety import (
    DeadMansSwitchError,
    enforce_triage_apply_rate_limit,
)
from app.services.template_engine import load_templates
from app.services.triage_service import TriageService
from app.services.triage_session import TriageSessionManager
from app.services.worker import get_redis_client
from app.services.ai_suggestions import invalidate_suggestion
from app.services.decision_journal import (
    DecisionJournalService,
    analysis_state,
    decision_to_legacy,
    serialize_decision,
    ticket_snapshot_fingerprint,
)

from app.services.host_telemetry import (  # noqa: F401
    get_task_telemetry,
    prefetch_task_telemetry,
)
from app.services.ai_synthesis import synthesize_triage_resolution  # noqa: F401

logger = logging.getLogger("core_api.routers.triage")

router = APIRouter(
    prefix="/api/v1/triage",
    tags=["Unified Triage Hub"],
    dependencies=[Depends(require_permission("triage:read"))],
)

# Экспорт для обратной совместимости с тестами
get_skipped_task_ids = TriageSessionManager.get_skipped_task_ids


# ---------------------------------------------------------------------------
# Pydantic Модели запросов / ответов
# ---------------------------------------------------------------------------


class ApplyTriageRequest(BaseModel):
    task_ids: list[int] = Field(
        ..., description="Список ID заявок для применения решения"
    )
    status_id: Literal[27, 29, 30, 35, 48] = Field(
        ..., description="Разрешенный целевой ID статуса"
    )
    comment: str = Field("", description="Текст комментария заявителю")
    is_private: bool = Field(False, description="Внутренний комментарий")
    expenses: int = Field(0, description="Списание трудозатрат в минутах")
    executor_ids: str = Field(
        settings.DEFAULT_EXECUTOR_IDS,
        description="ID исполнителей по умолчанию",
    )
    dry_run: bool = Field(False, description="Режим симуляции")
    confirmed_by_human: bool = Field(
        False,
        description="Явное подтверждение оператора для обхода аварийного лимита (Dead Man's Switch)",
    )
    verified_execution_job_id: str | None = Field(
        None,
        description=(
            "ID успешно завершенной команды Execution Worker. Обязателен для "
            "финализации заявок, требующих инфраструктурного действия."
        ),
    )
    decision_id: str | None = Field(None, description="ID зафиксированного решения")
    decision_version: int | None = Field(
        None, description="Версия зафиксированного решения"
    )


class SkipSessionRequest(BaseModel):
    task_ids: list[int] = Field(
        ..., description="Список ID заявок для пропуска в текущей смене"
    )
    reason: str = Field("operator_skipped", description="Причина пропуска")
    operator_id: str | None = Field(None, description="Идентификатор оператора")


class RAGSearchRequest(BaseModel):
    query: str = Field(..., description="Текст поискового запроса")
    limit: int = Field(3, ge=1, le=20, description="Лимит совпадений")
    profile: Literal["precise", "balanced", "broad"] = Field(
        "balanced", description="Версионируемый профиль качества поиска"
    )
    service_id: int | None = Field(
        None, description="ID раздела/услуги IntraService для приоритизации"
    )
    service_path: str | None = Field(
        None, description="Полный иерархический путь услуги"
    )


class RAGIndexRequest(BaseModel):
    task_id: int
    original_name: str
    problem: str
    solution: str
    service_id: int
    service_name: str
    status_name: str
    classification_data: dict[str, Any] | None = None
    service_path: str | None = None
    service_path_ids: list[int] | None = None


class RAGSyncRequest(BaseModel):
    days: int = Field(30, ge=1, le=365, description="Глубина выгрузки в днях")
    limit: int = Field(50, ge=1, le=500, description="Лимит выгрузки задач")


class AnalyzeBatchRequest(BaseModel):
    task_ids: list[int] = Field(..., min_length=1, max_length=500)


class AnalyzeBatchResponse(BaseModel):
    status: str
    batch_id: str
    total: int
    already_running: bool = False
    message: str


class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str
    total: int
    processed: int
    failed: int
    skipped: int
    progress_pct: int
    is_active: bool
    completed_task_ids: list[int]


class CancelBatchResponse(BaseModel):
    batch_id: str
    status: str
    message: str


_active_background_tasks: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# Эндпоинты триажа очереди и карточки задач
# ---------------------------------------------------------------------------


async def attach_durable_decision(
    *,
    card: dict[str, Any],
    task_id: int,
    db: AsyncSession,
    actor: str,
    analysis_fence: int,
    force: bool = False,
) -> DecisionRecord:
    suggestion = card.get("ai_suggestion") or {}
    record = await DecisionJournalService(db).record_triage(
        task_id=task_id,
        task=card.get("task") or {},
        history=card.get("history") or [],
        decision=card.get("suggested_action"),
        kb_matches=card.get("kb_matches") or [],
        ai_text=card.get("ai_suggested_resolution"),
        ai_metadata=card.get("ai_metadata")
        or {
            "model": None,
            "backend": None,
            "circuit": card.get("circuit"),
            "input_tokens": None,
            "output_tokens": None,
        },
        policy=(card.get("suggested_action") or {}).get("_resolution_policy")
        or suggestion.get("policy")
        or {},
        analysis_fence=analysis_fence,
        actor=actor,
        force=force,
    )
    steps = list(
        (
            await db.scalars(
                select(DecisionStep)
                .where(DecisionStep.decision_id == record.id)
                .order_by(DecisionStep.sequence)
            )
        ).all()
    )
    card["decision"] = serialize_decision(record, steps=steps)
    card["sources"] = record.source_json
    card["readiness"] = {
        "ready": bool(record.proposal_json.get("ready")),
        "missing_data": record.completeness_json.get("missing_data", []),
        "blocked_reasons": record.completeness_json.get("blocked_reasons", []),
        "stale": suggestion.get("state") == "stale",
    }
    card["analysis"] = analysis_state(
        record,
        task=card.get("task") or {},
        history=card.get("history") or [],
    )
    return record


async def attach_existing_decision(
    *,
    card: dict[str, Any],
    record: DecisionRecord | None,
    db: AsyncSession,
) -> dict[str, Any]:
    """Hydrate the compatibility DTO from durable data without recalculation."""
    card.setdefault("kb_matches", [])
    card.setdefault("telemetry", None)
    card.setdefault("ai_metadata", {})
    card.setdefault("sources", record.source_json if record else {})
    card["suggested_action"] = decision_to_legacy(record) if record else None
    card["decision_envelope"] = (
        (record.proposal_json or {}).get("decision_envelope") if record else None
    )
    card["ai_suggested_resolution"] = (
        (record.proposal_json or {}).get("comment") if record else None
    )
    journal = DecisionJournalService(db)
    last_attempt = await journal.latest_triage_attempt(
        int((card.get("task") or {}).get("Id") or (record.task_id if record else 0))
    )
    applied = False
    if record is not None:
        applied = (
            await db.scalar(
                select(CommandRecord.id)
                .where(
                    CommandRecord.decision_id == record.id,
                    CommandRecord.status == "succeeded",
                )
                .limit(1)
            )
        ) is not None
    card["analysis"] = analysis_state(
        record,
        task=card.get("task") or {},
        history=card.get("history") or [],
        applied=applied,
        last_attempt=last_attempt,
    )
    if record is None:
        card["decision"] = None
        card["readiness"] = {"ready": False, "blocked_reasons": ["not_analyzed"]}
        return card

    steps = list(
        (
            await db.scalars(
                select(DecisionStep)
                .where(DecisionStep.decision_id == record.id)
                .order_by(DecisionStep.sequence)
            )
        ).all()
    )
    card["decision"] = serialize_decision(record, steps=steps)
    card["readiness"] = {
        "ready": card["analysis"]["can_quick_apply"],
        "missing_data": (record.completeness_json or {}).get("missing_data", []),
        "blocked_reasons": (record.completeness_json or {}).get("blocked_reasons", []),
        "stale": card["analysis"]["freshness"] == "stale",
    }
    return card


async def acquire_analysis_lease(task_id: int) -> tuple[Any, str, str, int]:
    redis = get_redis_client()
    lock_key = f"lock:triage-analysis:{task_id}"
    try:
        fence = await redis.incr(f"fence:triage-analysis:{task_id}")
        owner = f"{fence}:{uuid.uuid4()}"
        acquired = await redis.set(lock_key, owner, nx=True, ex=300)
    except Exception as exc:
        logger.warning("Analysis lock unavailable for task %s: %s", task_id, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "analysis_lock_unavailable"
        ) from exc
    if not acquired:
        raise HTTPException(status.HTTP_409_CONFLICT, "analysis_already_running")
    return redis, lock_key, owner, fence


async def release_analysis_lease(redis: Any, lock_key: str, owner: str) -> None:
    try:
        await redis.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            lock_key,
            owner,
        )
    except Exception:
        logger.warning("Failed to release analysis lease %s", lock_key, exc_info=True)


async def run_explicit_analysis(
    *,
    task_id: int,
    service_auth_b64: str,
    actor: str,
    db: AsyncSession,
    force: bool,
) -> dict[str, Any]:
    redis, lock_key, owner, fence = await acquire_analysis_lease(task_id)
    snapshot: dict[str, Any] | None = None
    try:
        snapshot = await TriageService.get_task_card_snapshot(service_auth_b64, task_id)
        if snapshot is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket_not_found")
        if not force:
            existing = await DecisionJournalService(db).latest_triage(task_id)
            last_attempt = await DecisionJournalService(db).latest_triage_attempt(
                task_id
            )
            existing_state = analysis_state(
                existing,
                task=snapshot["task"],
                history=snapshot["history"],
                last_attempt=last_attempt,
            )
            if (
                existing
                and existing_state["state"] == "ready"
                and existing_state["freshness"] == "current"
            ):
                return await attach_existing_decision(
                    card=snapshot, record=existing, db=db
                )

        card = await TriageService.get_task_card_details(
            service_auth_b64=service_auth_b64,
            db=db,
            task_id=task_id,
            force=force,
        )
        if not card:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ticket_not_found")
        source_fingerprint = ticket_snapshot_fingerprint(
            card.get("task") or {}, card.get("history") or []
        )
        current = await TriageService.get_task_card_snapshot(service_auth_b64, task_id)
        if current is None or source_fingerprint != ticket_snapshot_fingerprint(
            current["task"], current["history"]
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "ticket_changed_during_analysis"
            )
        if await redis.get(lock_key) != owner:
            raise HTTPException(status.HTTP_409_CONFLICT, "analysis_lease_lost")
        await attach_durable_decision(
            card=card,
            task_id=task_id,
            db=db,
            actor=actor,
            analysis_fence=fence,
            force=force,
        )
        return card
    except HTTPException as exc:
        if exc.detail not in {"ticket_not_found", "analysis_lease_lost"} and snapshot:
            try:
                if await redis.get(lock_key) == owner:
                    await DecisionJournalService(db).record_triage_failure(
                        task_id=task_id,
                        task=snapshot["task"],
                        history=snapshot["history"],
                        error_code=str(exc.detail),
                        actor=actor,
                    )
            except Exception:
                logger.exception(
                    "Failed to journal analysis error for task %s", task_id
                )
        raise
    except Exception:
        if snapshot:
            try:
                if await redis.get(lock_key) == owner:
                    await DecisionJournalService(db).record_triage_failure(
                        task_id=task_id,
                        task=snapshot["task"],
                        history=snapshot["history"],
                        error_code="analysis_failed",
                        actor=actor,
                    )
            except Exception:
                logger.exception(
                    "Failed to journal analysis error for task %s", task_id
                )
        raise
    finally:
        await release_analysis_lease(redis, lock_key, owner)


@router.get("/batch", status_code=status.HTTP_200_OK)
async def get_triage_batch(
    filter_id: int = Query(984, description="ID фильтра очереди 1-й линии"),
    limit: int = Query(5, ge=1, le=500, description="Размер пачки заявок"),
    page: int = Query(1, ge=1, description="Номер страницы/пачки"),
    service_prefix: str | None = Query(
        None,
        description="Номер раздела (01..16, 2, 3, 6) или название сервиса",
    ),
    redirect_only: bool = Query(
        False, description="Выборка только заявок, требующих редиректа"
    ),
    include_skipped: bool = Query(
        False, description="Включить в выборку ранее пропущенные заявки"
    ),
    include_rag: bool = Query(
        False,
        description="Выполнять семантический RAG-поиск по прецедентам для всей пачки",
    ),
    service_auth_b64: str = Depends(get_service_auth_b64),
    username: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает подготовленную пачку заявок с авто-рекомендациями и телеметрией 0ms."""
    batch_data = await TriageService.prepare_triage_batch(
        service_auth_b64=service_auth_b64,
        db=db,
        filter_id=filter_id,
        limit=limit,
        page=page,
        service_prefix=service_prefix,
        redirect_only=redirect_only,
        include_skipped=include_skipped,
        include_rag=include_rag,
        operator_id=username,
        compute_recommendations=False,
    )
    if isinstance(batch_data, dict):
        try:
            from app.services.outage_detector import OutageDetector

            tasks_list = batch_data.get("tasks", [])
            outages = await OutageDetector.detect_outages(tasks_list)
            batch_data["outages"] = outages
        except Exception as e:
            logger.debug("Ошибка детекции аварий в пачке триажа: %s", e)
            batch_data["outages"] = []
    tasks = batch_data.get("tasks", []) if isinstance(batch_data, dict) else []
    scope_task_ids = list(batch_data.pop("scope_task_ids", []))
    all_scope_records = await DecisionJournalService(db).latest_triage_many(
        scope_task_ids
    )
    all_scope_attempts = await DecisionJournalService(db).latest_triage_attempt_many(
        scope_task_ids
    )
    records = {
        task_id: all_scope_records[task_id]
        for task_id in [int(item["task_id"]) for item in tasks]
        if task_id in all_scope_records
    }
    applied_decision_ids = set()
    if records:
        applied_decision_ids = set(
            (
                await db.scalars(
                    select(CommandRecord.decision_id).where(
                        CommandRecord.decision_id.in_(
                            [record.id for record in records.values()]
                        ),
                        CommandRecord.status == "succeeded",
                    )
                )
            ).all()
        )
    for item in tasks:
        record = records.get(int(item["task_id"]))
        item["suggested_action"] = decision_to_legacy(record) if record else None
        item["decision_envelope"] = (
            (record.proposal_json or {}).get("decision_envelope") if record else None
        )
        item["analysis"] = analysis_state(
            record,
            task=item.get("task") or {},
            applied=bool(record and record.id in applied_decision_ids),
            last_attempt=all_scope_attempts.get(int(item["task_id"])),
        )
        item["sources"] = record.source_json if record else {}
        item["readiness"] = {
            "ready": item["analysis"]["can_quick_apply"],
            "blocked_reasons": (
                (record.completeness_json or {}).get("blocked_reasons", [])
                if record
                else ["not_analyzed"]
            ),
        }
    if isinstance(batch_data, dict):
        batch_data["analysis_counts"] = {
            "analyzed": len(all_scope_records),
            "not_analyzed": max(0, len(scope_task_ids) - len(all_scope_records)),
            "scope_total": len(scope_task_ids),
            "is_complete_scope": not batch_data.get("is_truncated", False),
        }
    return batch_data


@router.get("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def get_task_details_card(
    task_id: int,
    service_auth_b64: str = Depends(get_service_auth_b64),
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает заявку и сохранённое решение без повторного анализа."""
    card = await TriageService.get_task_card_snapshot(service_auth_b64, task_id)
    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка #{task_id} не найдена в IntraService.",
        )
    record = await DecisionJournalService(db).latest_triage(task_id)
    return await attach_existing_decision(card=card, record=record, db=db)


@router.post(
    "/tasks/{task_id}/analyze",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def analyze_task_endpoint(
    task_id: int,
    service_auth_b64: str = Depends(get_service_auth_b64),
    operator: str = Depends(principal_subject),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    return await run_explicit_analysis(
        task_id=task_id,
        service_auth_b64=service_auth_b64,
        actor=operator,
        db=db,
        force=False,
    )


@router.post(
    "/tasks/{task_id}/reanalyze",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def reanalyze_task_endpoint(
    task_id: int,
    service_auth_b64: str = Depends(get_service_auth_b64),
    operator: str = Depends(principal_subject),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
):
    """Явно создаёт новую версию анализа; чтение заявки этого не делает."""
    return await run_explicit_analysis(
        task_id=task_id,
        service_auth_b64=service_auth_b64,
        actor=operator,
        db=db,
        force=True,
    )


async def _execute_triage_batch_worker(
    *,
    batch_id: str,
    task_ids: list[int],
    service_auth_b64: str,
    operator: str,
) -> None:
    redis = get_redis_client()
    semaphore = asyncio.Semaphore(settings.TRIAGE_ANALYSIS_MAX_CONCURRENCY)
    total = len(task_ids)
    start_time = time.time()
    abort_key = f"triage:batch:{batch_id}:abort"
    batch_key = f"triage:batch:{batch_id}"
    completed_set_key = f"triage:batch:{batch_id}:completed"

    async def analyze_single(task_id: int) -> dict[str, Any]:
        if await redis.exists(abort_key):
            return {"task_id": task_id, "status": "cancelled"}

        async with semaphore, AsyncSessionLocal() as session:
            if await redis.exists(abort_key):
                return {"task_id": task_id, "status": "cancelled"}
            try:
                card = await run_explicit_analysis(
                    task_id=task_id,
                    service_auth_b64=service_auth_b64,
                    actor=operator,
                    db=session,
                    force=False,
                )
                processed = await redis.hincrby(batch_key, "processed", 1)
                await redis.sadd(completed_set_key, str(task_id))
                await redis.hset(batch_key, "heartbeat", str(time.time()))

                failed = int(await redis.hget(batch_key, "failed") or 0)
                pct = int(min(100, round((processed + failed) / max(1, total) * 100)))

                event_payload = {
                    "event": "task_analyzed",
                    "batch_id": batch_id,
                    "task_id": task_id,
                    "status": "processed",
                    "analysis": card.get("analysis"),
                    "scenario_key": card.get("scenario_key"),
                    "progress": {
                        "processed": processed,
                        "failed": failed,
                        "total": total,
                        "pct": pct,
                    },
                }
                await redis.publish(
                    "events:all", json.dumps(event_payload, ensure_ascii=False)
                )
                return {"task_id": task_id, "status": "processed"}
            except HTTPException as exc:
                if exc.detail == "analysis_already_running":
                    await redis.hincrby(batch_key, "skipped", 1)
                    status_val = "skipped"
                else:
                    await redis.hincrby(batch_key, "failed", 1)
                    status_val = "failed"
                await redis.hset(batch_key, "heartbeat", str(time.time()))
                processed = int(await redis.hget(batch_key, "processed") or 0)
                failed = int(await redis.hget(batch_key, "failed") or 0)
                pct = int(min(100, round((processed + failed) / max(1, total) * 100)))

                event_payload = {
                    "event": "task_analyzed",
                    "batch_id": batch_id,
                    "task_id": task_id,
                    "status": status_val,
                    "error": str(exc.detail),
                    "progress": {
                        "processed": processed,
                        "failed": failed,
                        "total": total,
                        "pct": pct,
                    },
                }
                await redis.publish(
                    "events:all", json.dumps(event_payload, ensure_ascii=False)
                )
                return {
                    "task_id": task_id,
                    "status": status_val,
                    "error": str(exc.detail),
                }
            except Exception:
                logger.exception("Async batch analysis failed for task %s", task_id)
                failed = await redis.hincrby(batch_key, "failed", 1)
                await redis.hset(batch_key, "heartbeat", str(time.time()))
                processed = int(await redis.hget(batch_key, "processed") or 0)
                pct = int(min(100, round((processed + failed) / max(1, total) * 100)))

                event_payload = {
                    "event": "task_analyzed",
                    "batch_id": batch_id,
                    "task_id": task_id,
                    "status": "failed",
                    "error": "analysis_failed",
                    "progress": {
                        "processed": processed,
                        "failed": failed,
                        "total": total,
                        "pct": pct,
                    },
                }
                await redis.publish(
                    "events:all", json.dumps(event_payload, ensure_ascii=False)
                )
                return {
                    "task_id": task_id,
                    "status": "failed",
                    "error": "analysis_failed",
                }

    try:
        await asyncio.gather(*(analyze_single(tid) for tid in task_ids))
    except asyncio.CancelledError:
        logger.warning("Batch %s cancelled via coroutine cancellation", batch_id)
    except Exception as err:
        logger.exception("Unexpected error in batch %s: %s", batch_id, err)
    finally:
        is_aborted = bool(await redis.exists(abort_key))
        final_status = "cancelled" if is_aborted else "completed"
        processed = int(await redis.hget(batch_key, "processed") or 0)
        failed = int(await redis.hget(batch_key, "failed") or 0)
        skipped = int(await redis.hget(batch_key, "skipped") or 0)
        elapsed = round(time.time() - start_time, 2)

        await redis.hset(
            batch_key,
            mapping={
                "status": final_status,
                "elapsed_seconds": str(elapsed),
                "heartbeat": str(time.time()),
            },
        )
        active_id = await redis.get("triage:batch:active_id")
        if active_id == batch_id:
            await redis.delete("triage:batch:active_id")

        if is_aborted:
            final_event = {
                "event": "batch_cancelled",
                "batch_id": batch_id,
                "total": total,
                "processed": processed,
                "failed": failed,
                "elapsed_seconds": elapsed,
            }
        else:
            final_event = {
                "event": "batch_completed",
                "batch_id": batch_id,
                "total": total,
                "processed": processed,
                "failed": failed,
                "skipped": skipped,
                "elapsed_seconds": elapsed,
            }
        await redis.publish("events:all", json.dumps(final_event, ensure_ascii=False))
        logger.info(
            "Batch %s finalized (%s): %d processed, %d failed, %d skipped in %.2fs",
            batch_id,
            final_status,
            processed,
            failed,
            skipped,
            elapsed,
        )


async def cleanup_stale_triage_batches(redis: Any) -> None:
    try:
        active_id = await redis.get("triage:batch:active_id")
        if not active_id:
            return
        batch_key = f"triage:batch:{active_id}"
        data = await redis.hgetall(batch_key)
        if not data:
            await redis.delete("triage:batch:active_id")
            return
        heartbeat = float(data.get("heartbeat") or data.get("created_at") or 0)
        if time.time() - heartbeat > 30.0:
            logger.warning(
                "Stale active triage batch %s detected on startup (heartbeat age: %.1fs). Marking as interrupted.",
                active_id,
                time.time() - heartbeat,
            )
            await redis.hset(batch_key, "status", "interrupted")
            await redis.delete("triage:batch:active_id")
    except Exception as exc:
        logger.warning("Error cleaning up stale triage batches: %s", exc)


@router.post(
    "/analyze-batch",
    response_model=AnalyzeBatchResponse,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def analyze_batch_endpoint(
    payload: AnalyzeBatchRequest,
    response: Response,
    service_auth_b64: str = Depends(get_service_auth_b64),
    operator: str = Depends(principal_subject),
    _origin: None = Depends(verify_trusted_origin),
):
    clean_task_ids = [tid for tid in dict.fromkeys(payload.task_ids) if tid > 0]
    if not clean_task_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_valid_task_ids")

    redis = get_redis_client()

    # Проверка Single Flight: если батч уже активен, возвращаем его
    active_id = await redis.get("triage:batch:active_id")
    if active_id:
        active_status = await redis.hget(f"triage:batch:{active_id}", "status")
        if active_status == "running":
            total_raw = await redis.hget(f"triage:batch:{active_id}", "total")
            active_total = int(total_raw) if total_raw else len(clean_task_ids)
            response.status_code = status.HTTP_200_OK
            return AnalyzeBatchResponse(
                status="already_running",
                batch_id=active_id,
                total=active_total,
                already_running=True,
                message="Пакетный анализ уже выполняется",
            )

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    await redis.set("triage:batch:active_id", batch_id, ex=1800)
    batch_key = f"triage:batch:{batch_id}"
    await redis.hset(
        batch_key,
        mapping={
            "batch_id": batch_id,
            "total": str(len(clean_task_ids)),
            "processed": "0",
            "failed": "0",
            "skipped": "0",
            "status": "running",
            "created_at": str(time.time()),
            "heartbeat": str(time.time()),
            "operator": operator,
        },
    )
    await redis.expire(batch_key, 1800)
    await redis.expire(f"triage:batch:{batch_id}:completed", 1800)

    task = asyncio.create_task(
        _execute_triage_batch_worker(
            batch_id=batch_id,
            task_ids=clean_task_ids,
            service_auth_b64=service_auth_b64,
            operator=operator,
        )
    )
    _active_background_tasks.add(task)
    task.add_done_callback(_active_background_tasks.discard)

    response.status_code = status.HTTP_202_ACCEPTED
    return AnalyzeBatchResponse(
        status="accepted",
        batch_id=batch_id,
        total=len(clean_task_ids),
        already_running=False,
        message="Пакетный анализ запущен в фоновом режиме",
    )


@router.get(
    "/analyze-batch/{batch_id}",
    response_model=BatchStatusResponse,
    dependencies=[Depends(require_permission("triage:read"))],
)
async def get_analyze_batch_status_endpoint(batch_id: str):
    redis = get_redis_client()
    batch_key = f"triage:batch:{batch_id}"
    data = await redis.hgetall(batch_key)
    if not data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch_not_found")

    total = int(data.get("total") or 0)
    processed = int(data.get("processed") or 0)
    failed = int(data.get("failed") or 0)
    skipped = int(data.get("skipped") or 0)
    status_val = data.get("status") or "unknown"
    pct = int(min(100, round((processed + failed) / max(1, total) * 100)))

    completed_raw = await redis.smembers(f"triage:batch:{batch_id}:completed")
    completed_task_ids = [int(tid) for tid in completed_raw if tid.isdigit()]

    return BatchStatusResponse(
        batch_id=batch_id,
        status=status_val,
        total=total,
        processed=processed,
        failed=failed,
        skipped=skipped,
        progress_pct=pct,
        is_active=status_val == "running",
        completed_task_ids=completed_task_ids,
    )


@router.post(
    "/analyze-batch/{batch_id}/cancel",
    response_model=CancelBatchResponse,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def cancel_analyze_batch_endpoint(
    batch_id: str,
    _origin: None = Depends(verify_trusted_origin),
):
    redis = get_redis_client()
    batch_key = f"triage:batch:{batch_id}"
    if not await redis.exists(batch_key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "batch_not_found")

    await redis.set(f"triage:batch:{batch_id}:abort", "1", ex=300)
    active_id = await redis.get("triage:batch:active_id")
    if active_id == batch_id:
        await redis.delete("triage:batch:active_id")

    return CancelBatchResponse(
        batch_id=batch_id,
        status="cancelling",
        message="Сигнал отмены передан воркеру",
    )



def extract_operator_user_id(
    authorization: str | None = None,
    admin_session: str | None = None,
) -> int | None:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer_val = authorization[7:].strip()
        if bearer_val and bearer_val != "sso_session":
            token = bearer_val
    elif admin_session:
        token = admin_session.strip()

    if token:
        for sec in [settings.JWT_SECRET]:
            if not sec:
                continue
            try:
                payload = jwt.decode(token, sec, algorithms=["HS256"])
                uid = payload.get("user_id")
                if uid:
                    return int(uid)
            except Exception:
                pass
    return None


@router.post(
    "/apply",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def apply_triage_action(
    payload: ApplyTriageRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
    _origin: None = Depends(verify_trusted_origin),
    db: AsyncSession = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
    admin_session: str | None = Cookie(None),
):
    """Атомарное применение решения к группе заявок (с защитой Dead Man's Switch)."""
    if not payload.task_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Список task_ids не может быть пустым.",
        )

    if not payload.dry_run:
        active_task_ids = list(
            (
                await db.scalars(
                    select(TicketRun.task_id).where(
                        TicketRun.task_id.in_(payload.task_ids),
                        TicketRun.completed_at.is_(None),
                    )
                )
            ).all()
        )
        if active_task_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Заявка управляется активным TicketRun; создайте связанную "
                    f"команду через /api/v2/commands: {sorted(active_task_ids)}"
                ),
            )

    # Проверка аварийного лимита Dead Man's Switch
    if not payload.dry_run:
        try:
            await enforce_triage_apply_rate_limit(
                ticket_count=len(payload.task_ids),
                confirmed_by_human=payload.confirmed_by_human,
            )
        except DeadMansSwitchError as e:
            logger.warning("Аварийный тормоз (Dead Man's Switch) сработал: %s", e)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(e),
            )

    op_user_id = extract_operator_user_id(authorization, admin_session)
    decision_by_task: dict[int, DecisionRecord] = {}
    if not payload.dry_run:
        journal = DecisionJournalService(db)
        if payload.decision_id is not None:
            if len(payload.task_ids) != 1 or payload.decision_version is None:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "decision_version_required_for_single_task",
                )
            try:
                parsed_decision_id = uuid.UUID(payload.decision_id)
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_decision_id"
                ) from exc
            decision_by_task[payload.task_ids[0]] = await journal.require_current(
                decision_id=parsed_decision_id,
                task_id=payload.task_ids[0],
                version=payload.decision_version,
            )
            current_task = await intraservice.get_single_task(
                service_auth_b64, payload.task_ids[0]
            )
            current_history_payload = await intraservice.get_task_lifetime(
                service_auth_b64, payload.task_ids[0]
            )
            current_history = (
                current_history_payload.get("TaskLifetimes", [])
                if isinstance(current_history_payload, dict)
                else (current_history_payload or [])
            )
            expected_fingerprint = decision_by_task[
                payload.task_ids[0]
            ].context_json.get("ticket_fingerprint")
            if not current_task or expected_fingerprint != ticket_snapshot_fingerprint(
                current_task, current_history
            ):
                raise HTTPException(status.HTTP_409_CONFLICT, "decision_stale")
        else:
            for task_id in payload.task_ids:
                decision_by_task[task_id] = await journal.record_operational(
                    task_id=task_id,
                    ticket_run_id=None,
                    action="apply_triage",
                    target={"task_id": task_id},
                    parameters={
                        "status_id": payload.status_id,
                        "comment": payload.comment,
                        "expenses": payload.expenses,
                        "executor_ids": payload.executor_ids,
                    },
                    actor=str(op_user_id or "operator"),
                )
            await db.commit()
    results = await TriageService.apply_triage_resolution(
        service_auth_b64=service_auth_b64,
        db=db,
        task_ids=payload.task_ids,
        status_id=payload.status_id,
        comment=payload.comment,
        expenses=payload.expenses,
        executor_ids=payload.executor_ids,
        dry_run=payload.dry_run,
        operator_user_id=op_user_id,
        verified_execution_job_id=payload.verified_execution_job_id,
        is_private=payload.is_private,
    )

    # Если ни одна задача не была успешно обновлена в IntraService, возвращаем ошибку клиенту
    if (
        not payload.dry_run
        and results
        and all(not r.get("update_ok", False) for r in results)
    ):
        first_err = (
            results[0].get("error")
            or "Не удалось обновить заявку в IntraService (проверьте доступные переходы статусов и права роли)."
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=first_err,
        )

    if not payload.dry_run:
        redis = get_redis_client()
        for result in results:
            if result.get("update_ok"):
                decision = decision_by_task.get(int(result["task_id"]))
                if decision is not None:
                    proposal = decision.proposal_json or {}
                    proposed_parameters = proposal.get("parameters") or proposal
                    proposed_comment = proposed_parameters.get("comment")
                    proposed_status = proposed_parameters.get("status_id")
                    verdict = (
                        "accepted"
                        if proposed_comment == payload.comment
                        and proposed_status == payload.status_id
                        else "modified"
                    )
                    await DecisionJournalService(db).add_feedback(
                        decision_id=decision.id,
                        verdict=verdict,
                        reason_code=None,
                        comment=None,
                        final_action={
                            "status_id": payload.status_id,
                            "comment": payload.comment,
                            "expenses": payload.expenses,
                        },
                        actor=str(op_user_id or "operator"),
                    )
                await invalidate_suggestion(redis, int(result["task_id"]))

        # Публикуем событие применения триажа в шину событий SSE
        if results and any(r.get("update_ok", False) for r in results):
            try:
                event_payload = {
                    "event": "triage_applied",
                    "task_ids": payload.task_ids,
                    "status_id": payload.status_id,
                    "operator_user_id": op_user_id,
                    "timestamp": time.time(),
                }
                await redis.publish(
                    "events:all", json.dumps(event_payload, ensure_ascii=False)
                )
            except Exception as ex:
                logger.debug(
                    "Не удалось опубликовать событие triage_applied в Redis: %s", ex
                )
    return {"results": results}


@router.get("/duplicates", status_code=status.HTTP_200_OK)
async def get_duplicates_in_queue(
    filter_id: int = Query(984, description="ID фильтра очереди"),
    limit: int = Query(10, description="Максимальное число дубликатов"),
    service_auth_b64: str = Depends(get_service_auth_b64),
):
    """Поиск и группировка заявок-дубликатов в очереди 1-й линии."""
    duplicates = await TriageService.find_queue_duplicates(
        service_auth_b64=service_auth_b64,
        filter_id=filter_id,
        limit=limit,
    )
    return {"total": len(duplicates), "duplicates": duplicates}


# ---------------------------------------------------------------------------
# Сессионное состояние оператора (пропуск заявок)
# ---------------------------------------------------------------------------


@router.post(
    "/session/skip",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def skip_session_tasks(
    payload: SkipSessionRequest,
    operator: str = Depends(principal_subject),
):
    """Помечает заявки как пропущенные в текущей смене оператора."""
    op = payload.operator_id or operator
    skipped_count = await TriageSessionManager.skip_tasks(
        task_ids=payload.task_ids,
        operator_id=op,
    )
    return {
        "status": "success",
        "skipped_count": skipped_count,
        "operator": op,
    }


@router.post(
    "/session/reset",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def reset_session_tasks(
    operator_id: str | None = Query(None, description="Идентификатор оператора"),
    operator: str = Depends(principal_subject),
):
    """Сбрасывает сессионный кэш пропущенных заявок."""
    op = operator_id or operator
    await TriageSessionManager.reset_session(operator_id=op)
    return {"status": "success", "message": "Сессия сброшена", "operator": op}


# ---------------------------------------------------------------------------
# Справочники каталога и сервисов
# ---------------------------------------------------------------------------


@router.get("/services", status_code=status.HTTP_200_OK)
async def get_root_services():
    """Возвращает список корневых разделов каталога услуг IntraService (01..16)."""
    return [
        {"root_number": k, "id": v["id"], "name": v["name"]}
        for k, v in sorted(ROOT_SERVICES.items())
    ]


@router.get("/catalog", status_code=status.HTTP_200_OK)
async def get_full_catalog(
    search: str | None = Query(None, description="Поисковый фильтр"),
    service_auth_b64: str = Depends(get_service_auth_b64),
):
    """Возвращает полный каталог услуг IntraService с опциональным поиском."""
    catalog = await intraservice.get_services(service_auth_b64) or []
    if search:
        q = search.lower()
        catalog = [s for s in catalog if q in (s.get("Name") or "").lower()]
    return catalog


@router.get("/templates", status_code=status.HTTP_200_OK)
async def get_triage_templates():
    """Возвращает все доступные шаблоны ответов инженера."""
    return load_templates()


# ---------------------------------------------------------------------------
# RAG-делегаты (для обратной совместимости с клиентами и тестами)
# ---------------------------------------------------------------------------


@router.post(
    "/rag/search",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("ai:use"))],
)
async def rag_search_endpoint(
    payload: RAGSearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Поиск похожих решений в векторной базе PostgreSQL pgvector."""
    profiles = {
        "precise": {"distance": 0.35, "rerank": 0.90},
        "balanced": {"distance": 0.70, "rerank": 0.85},
        "broad": {"distance": 0.90, "rerank": 0.65},
    }
    profile = profiles[payload.profile]
    matches = await search_knowledge_base(
        db=db,
        query_text=payload.query,
        limit=payload.limit,
        distance_threshold=profile["distance"],
        rerank_threshold=profile["rerank"],
        service_id=payload.service_id,
        service_path=payload.service_path,
    )
    return {"total": len(matches), "matches": matches}


@router.post(
    "/rag/index",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def rag_index_endpoint(
    payload: RAGIndexRequest,
    db: AsyncSession = Depends(get_db),
):
    """Индексация решения задачи в векторную базу PostgreSQL pgvector."""
    ok = await index_task_knowledge(
        db=db,
        task_id=payload.task_id,
        original_name=payload.original_name,
        problem=payload.problem,
        solution=payload.solution,
        service_id=payload.service_id,
        service_name=payload.service_name,
        status_name=payload.status_name,
        classification_data=payload.classification_data,
        service_path=payload.service_path,
        service_path_ids=payload.service_path_ids,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось проиндексировать задачу #{payload.task_id} в RAG.",
        )
    return {"status": "success", "task_id": payload.task_id}


@router.post(
    "/rag/backfill-paths",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def rag_backfill_paths_endpoint(
    db: AsyncSession = Depends(get_db),
):
    """Фоновое обогащение существующих записей базы знаний полными путями каталога услуг."""
    from app.services.rag import backfill_kb_service_paths

    updated = await backfill_kb_service_paths(db)
    return {"status": "success", "updated_records": updated}


@router.post(
    "/rag/sync",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_permission("triage:mutate"))],
)
async def rag_sync_endpoint(
    payload: RAGSyncRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
    db: AsyncSession = Depends(get_db),
):
    """Синхронизация закрытых заявок из IntraService в векторную базу pgvector."""
    return await sync_historical_closed_tasks(
        auth_b64=service_auth_b64,
        db=db,
        days=payload.days,
        limit=payload.limit,
    )



@router.get("/feedback-review", status_code=status.HTTP_200_OK)
async def get_feedback_review_endpoint(
    limit: int = Query(20, ge=1, le=100, description="Количество записей аудита"),
    db: AsyncSession = Depends(get_db),
):
    """Compatibility view backed by the durable decision feedback journal."""
    from sqlalchemy import desc, select
    from app.database.db import DecisionFeedback, DecisionRecord

    query = (
        select(DecisionFeedback, DecisionRecord)
        .join(DecisionRecord, DecisionRecord.id == DecisionFeedback.decision_id)
        .order_by(desc(DecisionFeedback.created_at))
        .limit(limit)
    )
    res = await db.execute(query)
    entries = res.all()

    return {
        "total": len(entries),
        "items": [
            {
                "id": str(feedback.id),
                "decision_id": str(decision.id),
                "task_id": decision.task_id,
                "verdict": feedback.verdict,
                "reason_code": feedback.reason_code,
                "final_action": feedback.final_action_json,
                "operator_id": feedback.actor,
                "created_at": feedback.created_at.isoformat()
                if feedback.created_at
                else None,
            }
            for feedback, decision in entries
        ],
    }
