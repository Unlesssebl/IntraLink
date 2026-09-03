"""
Роутер централизованного триажа и пакетной обработки очередей для Web UI и Helpdesk Agent.
Спроектирован как тонкий контроллер (SRP), делегирующий логику в TriageService и TriageSessionManager.
"""

import logging
from typing import Any, Literal
import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import get_db
from app.routers.deps import (
    OperatorContext,
    get_operator_context,
    get_service_auth_b64,
    verify_admin_or_api_key,
)
from app.services import intraservice
from app.services.ai_synthesis import synthesize_triage_resolution
from app.services.host_telemetry import (
    get_task_telemetry,
    prefetch_task_telemetry,
)
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
from app.services.template_engine import (
    auto_detect_template,
    detect_service_redirect,
    load_templates,
)
from app.services.triage_service import TriageService
from app.services.triage_session import TriageSessionManager
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.routers.triage")

router = APIRouter(
    prefix="/api/v1/triage",
    tags=["Unified Triage Hub"],
    dependencies=[Depends(verify_admin_or_api_key)],
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


class SkipSessionRequest(BaseModel):
    task_ids: list[int] = Field(
        ..., description="Список ID заявок для пропуска в текущей смене"
    )
    reason: str = Field("operator_skipped", description="Причина пропуска")
    operator_id: str | None = Field(None, description="Идентификатор оператора")


class RAGSearchRequest(BaseModel):
    query: str = Field(..., description="Текст поискового запроса")
    limit: int = Field(3, description="Лимит совпадений")
    threshold: float = Field(0.70, description="Порог косинусного расстояния")


class RAGIndexRequest(BaseModel):
    task_id: int
    original_name: str
    problem: str
    solution: str
    service_id: int
    service_name: str
    status_name: str
    classification_data: dict[str, Any] | None = None


class RAGSyncRequest(BaseModel):
    days: int = Field(30, ge=1, le=365, description="Глубина выгрузки в днях")
    limit: int = Field(50, ge=1, le=500, description="Лимит выгрузки задач")


# ---------------------------------------------------------------------------
# Эндпоинты триажа очереди и карточки задач
# ---------------------------------------------------------------------------


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
    username: str = Depends(verify_admin_or_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает подготовленную пачку заявок с авто-рекомендациями и телеметрией 0ms."""
    return await TriageService.prepare_triage_batch(
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


@router.get("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def get_task_details_card(
    task_id: int,
    service_auth_b64: str = Depends(get_service_auth_b64),
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
    return card


@router.post("/tasks/{task_id}/reanalyze", status_code=status.HTTP_200_OK)
async def reanalyze_task_endpoint(
    task_id: int,
    service_auth_b64: str = Depends(get_service_auth_b64),
    operator: str = Depends(verify_admin_or_api_key),
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
        for sec in [settings.ADMIN_JWT_SECRET, settings.JWT_SECRET, "intralink-admin-secret"]:
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


@router.post("/apply", status_code=status.HTTP_200_OK)
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
    )

    # Если ни одна задача не была успешно обновлена в IntraService, возвращаем ошибку клиенту
    if not payload.dry_run and results and all(not r.get("update_ok", False) for r in results):
        first_err = results[0].get("error") or "Не удалось обновить заявку в IntraService (проверьте доступные переходы статусов и права роли)."
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=first_err,
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


@router.post("/session/skip", status_code=status.HTTP_200_OK)
async def skip_session_tasks(
    payload: SkipSessionRequest,
    operator: str = Depends(verify_admin_or_api_key),
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


@router.post("/session/reset", status_code=status.HTTP_200_OK)
async def reset_session_tasks(
    operator_id: str | None = Query(None, description="Идентификатор оператора"),
    operator: str = Depends(verify_admin_or_api_key),
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


@router.post("/rag/search", status_code=status.HTTP_200_OK)
async def rag_search_endpoint(
    payload: RAGSearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Поиск похожих решений в векторной базе PostgreSQL pgvector."""
    matches = await search_knowledge_base(
        db=db,
        query_text=payload.query,
        limit=payload.limit,
        distance_threshold=payload.threshold,
    )
    return {"total": len(matches), "matches": matches}


@router.post("/rag/index", status_code=status.HTTP_200_OK)
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
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось проиндексировать задачу #{payload.task_id} в RAG.",
        )
    return {"status": "success", "task_id": payload.task_id}


@router.post("/rag/sync", status_code=status.HTTP_200_OK)
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


@router.post("/cache/purge", status_code=status.HTTP_200_OK)
async def purge_triage_cache_endpoint(
    operator: str = Depends(verify_admin_or_api_key),
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
    min_diff: float = Query(0.0, ge=0.0, le=1.0, description="Минимальный коэффициент расхождения"),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает журнал аудита решений и расхождений для анализа качества (Feedback Loop)."""
    from sqlalchemy import desc, select
    from app.database.db import TriageAuditLog

    query = (
        select(TriageAuditLog)
        .where(TriageAuditLog.diff_ratio >= min_diff)
        .order_by(desc(TriageAuditLog.created_at))
        .limit(limit)
    )
    res = await db.execute(query)
    entries = res.scalars().all()

    return {
        "total": len(entries),
        "items": [
            {
                "id": str(e.id),
                "task_id": e.task_id,
                "generated_comment": e.generated_comment,
                "final_comment": e.final_comment,
                "confidence_score": e.confidence_score,
                "diff_ratio": e.diff_ratio,
                "operator_id": e.operator_id,
                "status_id": e.status_id,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
    }


