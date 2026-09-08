"""
Роутер централизованного триажа и пакетной обработки очередей для Web UI и Helpdesk Agent.
Спроектирован как тонкий контроллер (SRP), делегирующий логику в TriageService и TriageSessionManager.
"""

import json
import logging
import time
import uuid
from typing import Any, Literal
import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import DecisionRecord, DecisionStep, TicketRun, get_db
from app.routers.deps import (
    get_service_auth_b64,
    principal_subject,
    require_permission,
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
from app.services.ai_suggestions import build_suggestion_state, invalidate_suggestion
from app.services.decision_journal import (
    DecisionJournalService,
    serialize_decision,
    ticket_snapshot_fingerprint,
)

logger = logging.getLogger("core_api.routers.triage")

router = APIRouter(
    prefix="/api/v1/triage",
    tags=["Unified Triage Hub"],
    dependencies=[Depends(require_permission("triage:read"))],
)

# Экспорт для обратной совместимости с тестами
get_skipped_task_ids = TriageSessionManager.get_skipped_task_ids
from app.services.host_telemetry import (  # noqa: F401
    get_task_telemetry,
    prefetch_task_telemetry,
)
from app.services.ai_synthesis import synthesize_triage_resolution  # noqa: F401


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
    decision_version: int | None = Field(None, description="Версия зафиксированного решения")


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
    service_id: int | None = Field(None, description="ID раздела/услуги IntraService для приоритизации")
    service_path: str | None = Field(None, description="Полный иерархический путь услуги")


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


# ---------------------------------------------------------------------------
# Эндпоинты триажа очереди и карточки задач
# ---------------------------------------------------------------------------


async def attach_durable_decision(
    *,
    card: dict[str, Any],
    task_id: int,
    db: AsyncSession,
    actor: str,
    force: bool = False,
) -> None:
    suggestion = card.get("ai_suggestion") or {}
    record = await DecisionJournalService(db).record_triage(
        task_id=task_id,
        task=card.get("task") or {},
        history=card.get("history") or [],
        decision=card.get("suggested_action"),
        kb_matches=card.get("kb_matches") or [],
        ai_text=card.get("ai_suggested_resolution"),
            ai_metadata=card.get("ai_metadata") or {
                "model": None,
                "backend": None,
                "circuit": card.get("circuit"),
                "input_tokens": None,
                "output_tokens": None,
            },
        policy=(card.get("suggested_action") or {}).get("_resolution_policy")
        or suggestion.get("policy")
        or {},
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
        False, description="Выполнять семантический RAG-поиск по прецедентам для всей пачки"
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
    return batch_data


@router.get("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def get_task_details_card(
    task_id: int,
    service_auth_b64: str = Depends(get_service_auth_b64),
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает расширенную карточку задачи с историей, RAG и AI-синтезом решения."""
    card = await TriageService.get_task_card_details(
        service_auth_b64=service_auth_b64,
        db=db,
        task_id=task_id,
    )
    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка #{task_id} не найдена в IntraService.",
        )
    card["ai_suggestion"] = await build_suggestion_state(
        redis_client=get_redis_client(),
        task_id=task_id,
        task=card.get("task") or {},
        history=card.get("history"),
        decision=card.get("suggested_action"),
    )
    await attach_durable_decision(
        card=card, task_id=task_id, db=db, actor=operator
    )
    return card


@router.post("/tasks/{task_id}/reanalyze", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("triage:mutate"))])
async def reanalyze_task_endpoint(
    task_id: int,
    service_auth_b64: str = Depends(get_service_auth_b64),
    operator: str = Depends(principal_subject),
    db: AsyncSession = Depends(get_db),
):
    """Принудительно сбрасывает кэш и перезапускает RuleEngine/RAG/LLM анализ по заявке."""
    card = await TriageService.get_task_card_details(
        service_auth_b64=service_auth_b64,
        db=db,
        task_id=task_id,
        force=True,
    )
    if not card:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка #{task_id} не найдена в IntraService.",
        )
    card["ai_suggestion"] = await build_suggestion_state(
        redis_client=get_redis_client(),
        task_id=task_id,
        task=card.get("task") or {},
        history=card.get("history"),
        decision=card.get("suggested_action"),
        force_recalculate=True,
    )
    await attach_durable_decision(
        card=card, task_id=task_id, db=db, actor=operator, force=True
    )
    return card


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


@router.post("/apply", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("triage:mutate"))])
async def apply_triage_action(
    payload: ApplyTriageRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
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
            expected_fingerprint = decision_by_task[payload.task_ids[0]].context_json.get(
                "ticket_fingerprint"
            )
            if (
                not current_task
                or expected_fingerprint
                != ticket_snapshot_fingerprint(current_task, current_history)
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
    if not payload.dry_run and results and all(not r.get("update_ok", False) for r in results):
        first_err = results[0].get("error") or "Не удалось обновить заявку в IntraService (проверьте доступные переходы статусов и права роли)."
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
                await redis.publish("events:all", json.dumps(event_payload, ensure_ascii=False))
            except Exception as ex:
                logger.debug("Не удалось опубликовать событие triage_applied в Redis: %s", ex)
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


@router.post("/session/skip", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("triage:mutate"))])
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


@router.post("/session/reset", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("triage:mutate"))])
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


@router.post("/rag/search", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("ai:use"))])
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


@router.post("/rag/index", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("triage:mutate"))])
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


@router.post("/rag/backfill-paths", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("triage:mutate"))])
async def rag_backfill_paths_endpoint(
    db: AsyncSession = Depends(get_db),
):
    """Фоновое обогащение существующих записей базы знаний полными путями каталога услуг."""
    from app.services.rag import backfill_kb_service_paths
    updated = await backfill_kb_service_paths(db)
    return {"status": "success", "updated_records": updated}


@router.post("/rag/sync", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("triage:mutate"))])
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


@router.post("/cache/purge", status_code=status.HTTP_200_OK, dependencies=[Depends(require_permission("triage:mutate"))])
async def purge_triage_cache_endpoint(
    operator: str = Depends(principal_subject),
):
    """Глобальный сброс кэша вердиктов и резолюций AI/RuleEngine в Redis."""
    redis = get_redis_client()
    try:
        keys = await redis.keys("ai:resolution:*")
        deleted_count = 0
        if keys:
            deleted_count = await redis.delete(*keys)
        TriageService._catalog_cache = {}
        TriageService._catalog_cache_ts = 0.0
        return {
            "status": "ok",
            "deleted_verdicts": deleted_count,
            "message": "Кэш вердиктов и каталога успешно сброшен",
        }
    except Exception as e:
        logger.exception("Ошибка при очистке кэша вердиктов: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сброса кэша: {e}",
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
                "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
            }
            for feedback, decision in entries
        ],
    }


