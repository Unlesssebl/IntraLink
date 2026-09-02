"""
Роутер централизованного триажа, пакетной обработки очередей и RAG для Web UI и Helpdesk Agent.
"""

import asyncio
import json
import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import get_db
from app.routers.deps import get_service_auth_b64, verify_admin_or_api_key
from app.services import intraservice
from app.services.ai import RoutingMetadata, data_sanitizer
from app.services.deduplication import DuplicateDetector
from app.services.rag import (
    index_task_knowledge,
    search_knowledge_base,
    sync_historical_closed_tasks,
)
from app.services.rules.catalog import (
    ROOT_SERVICES,
    get_root_name,
    get_root_number_for_service_id,
)
from app.services.ai_synthesis import (
    canonize_task_solution,
    synthesize_triage_resolution,
)
from app.services.host_telemetry import (
    get_task_telemetry,
    prefetch_task_telemetry,
)
from app.services.safety import (
    DeadMansSwitchError,
    enforce_triage_apply_rate_limit,
)
from app.services.template_engine import (
    auto_detect_template,
    detect_service_redirect,
    load_templates,
)
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.routers.triage")

router = APIRouter(
    prefix="/api/v1/triage",
    tags=["Unified Triage Hub"],
    dependencies=[Depends(verify_admin_or_api_key)],
)


# ---------------------------------------------------------------------------
# Pydantic Модели запросов / ответов
# ---------------------------------------------------------------------------


class ApplyTriageRequest(BaseModel):
    task_ids: list[int] = Field(
        ..., description="Список ID заявок для применения решения"
    )
    status_id: int = Field(..., description="Целевой ID статуса")
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
# Вспомогательные функции работы с сессией в Redis
# ---------------------------------------------------------------------------


async def get_skipped_task_ids(operator_id: str | None = None) -> set[int]:
    """Возвращает множество ID пропущенных заявок из Redis для указанного оператора."""
    try:
        r = get_redis_client()
        keys_to_check = ["session:skipped_task_ids"]
        if operator_id:
            keys_to_check.insert(0, f"session:{operator_id}:skipped_task_ids")

        result = set()
        for key in keys_to_check:
            raw = await r.smembers(key)
            if raw:
                result.update({int(x) for x in raw if str(x).isdigit()})
        return result
    except Exception as e:
        logger.debug("Ошибка чтения session:skipped_task_ids из Redis: %s", e)
    return set()



# ---------------------------------------------------------------------------
# Эндпоинты триажа очереди и задач
# ---------------------------------------------------------------------------


@router.get("/batch", status_code=status.HTTP_200_OK)
async def get_triage_batch(
    filter_id: int = Query(984, description="ID фильтра очереди 1-й линии"),
    limit: int = Query(5, ge=1, le=50, description="Размер пачки заявок"),
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
    service_auth_b64: str = Depends(get_service_auth_b64),
    db: AsyncSession = Depends(get_db),
):
    """
    Возвращает подготовленную пачку заявок с авто-подбором шаблонов Rule Engine,
    детекцией дубликатов и семантическим RAG контекстом.
    """
    fetch_limit = max(limit * 4, 40)
    tasks = await intraservice.get_tasks_by_filter(
        auth_b64=service_auth_b64,
        filter_id=filter_id,
        page=1,
        page_size=fetch_limit,
    )

    if not tasks:
        return {
            "total_open": 0,
            "filter_id": filter_id,
            "page": page,
            "tasks": [],
            "duplicates": [],
        }

    # Исключаем закрытые (29, 30) и пропущенные (если include_skipped=False)
    skipped_ids = set() if include_skipped else await get_skipped_task_ids()
    active_tasks = [
        t
        for t in tasks
        if t.get("Id") not in skipped_ids and t.get("StatusId") not in (29, 30)
    ]

    # Детекция дубликатов
    detector = DuplicateDetector()
    all_duplicates = detector.find_duplicates(active_tasks)
    dup_map = {d["duplicate_task_id"]: d for d in all_duplicates}

    # Фильтрация по разделу
    if service_prefix:
        p_clean = (
            service_prefix.strip()
            .zfill(2)
            if service_prefix.strip().isdigit() and len(service_prefix.strip()) == 1
            else service_prefix.strip()
        )
        filtered = []
        for t in active_tasks:
            s_id = t.get("ServiceId")
            root_num = get_root_number_for_service_id(s_id)
            s_name = (t.get("ServiceName") or "").lower()
            if root_num and root_num == p_clean:
                filtered.append(t)
            elif p_clean.lower() in s_name:
                filtered.append(t)
        active_tasks = filtered

    # Фильтрация только по редиректам
    if redirect_only:
        active_tasks = [t for t in active_tasks if detect_service_redirect(t)]

    # Пагинация
    start_idx = (page - 1) * limit
    page_tasks = active_tasks[start_idx : start_idx + limit]

    result_items = []
    for t in page_tasks:
        t_id = t.get("Id")
        # Поиск похожих решений в RAG базе знаний
        t_name = t.get("Name") or ""
        t_desc = t.get("Description") or ""
        query_text = f"{t_name}. {t_desc}".strip()
        kb_matches = await search_knowledge_base(
            db=db, query_text=query_text, limit=2, distance_threshold=0.70
        )

        # Вычисление рекомендуемого действия через Rule Engine
        decision = auto_detect_template(
            task=t,
            kb_matches=kb_matches,
            redirect_mode=redirect_only,
        )

        # Оценка контура безопасности данных
        circuit_dec = data_sanitizer.evaluate_circuit(
            prompt=query_text,
            metadata=RoutingMetadata(service_id=t.get("ServiceId")),
        )

        is_dup = t_id in dup_map
        dup_info = dup_map.get(t_id)

        # Экспресс-телеметрия хоста из кэша (0ms)
        telemetry = await get_task_telemetry(t_id)
        if telemetry is None:
            # Запускаем фоновый pre-fetch для заполнения кэша
            asyncio.create_task(prefetch_task_telemetry(t))

        meta = t.get("_field_meta") or {}
        result_items.append({
            "task": t,
            "task_id": t_id,
            "name": t.get("Name"),
            "created": t.get("Created"),
            "status_id": t.get("StatusId"),
            "status_name": t.get("StatusName"),
            "service_id": t.get("ServiceId"),
            "service_name": t.get("ServiceName"),
            "creator": t.get("Creator"),
            "creator_phone": meta.get("phone") or t.get("CreatorPhone") or "—",
            "pc_name": meta.get("pc_name") or "",
            "room": meta.get("room") or "",
            "has_attachments": t.get("_has_attachments", False),
            "attachments_count": len(t.get("_attachments_list", [])),
            "suggested_action": decision,
            "is_duplicate": is_dup,
            "duplicate_info": dup_info,
            "kb_matches": kb_matches,
            "telemetry": telemetry,
            "circuit": circuit_dec.circuit.value,
            "circuit_reason": circuit_dec.reason,
            "requires_sanitization": circuit_dec.requires_sanitization,
        })

    return {
        "total_open": len(active_tasks),
        "filter_id": filter_id,
        "page": page,
        "tasks": result_items,
        "duplicates": all_duplicates[:10],
    }


@router.get("/tasks/{task_id}", status_code=status.HTTP_200_OK)
async def get_task_details_card(
    task_id: int,
    service_auth_b64: str = Depends(get_service_auth_b64),
    db: AsyncSession = Depends(get_db),
):
    """
    Возвращает детальную карточку задачи с нормализованными полями,
    историей переписки, RAG-совпадениями, телеметрией и авто-рекомендацией.
    """
    task = await intraservice.get_single_task(service_auth_b64, task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Заявка #{task_id} не найдена в IntraService.",
        )

    # История переписки
    history = (
        await intraservice.get_task_lifetime(service_auth_b64, task_id) or []
    )

    # Экспресс-телеметрия хоста
    telemetry = await get_task_telemetry(task_id)
    if telemetry is None:
        telemetry = await prefetch_task_telemetry(task)

    # Поиск по RAG
    t_name = task.get("Name") or ""
    t_desc = task.get("Description") or ""
    query_text = f"{t_name}. {t_desc}".strip()
    kb_matches = await search_knowledge_base(
        db=db, query_text=query_text, limit=3, distance_threshold=0.70
    )

    # Рекомендация Rule Engine
    decision = auto_detect_template(task=task, kb_matches=kb_matches)

    # Оценка контура безопасности данных
    circuit_dec = data_sanitizer.evaluate_circuit(
        prompt=query_text,
        metadata=RoutingMetadata(service_id=task.get("ServiceId")),
    )

    # AI-синтез решения от имени инженера
    ai_resolution = await synthesize_triage_resolution(
        task=task,
        kb_matches=kb_matches,
        telemetry=telemetry,
        circuit=circuit_dec.circuit,
    )

    return {
        "task": task,
        "history": history,
        "kb_matches": kb_matches,
        "telemetry": telemetry,
        "suggested_action": decision,
        "ai_suggested_resolution": ai_resolution,
        "circuit": circuit_dec.circuit.value,
        "circuit_reason": circuit_dec.reason,
        "requires_sanitization": circuit_dec.requires_sanitization,
    }


@router.post("/apply", status_code=status.HTTP_200_OK)
async def apply_triage_action(
    payload: ApplyTriageRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
    db: AsyncSession = Depends(get_db),
):
    """
    Атомарное применение решения к заявке или группе заявок:
    1. Перевод в статус 27 (В работе) с назначением исполнителя.
    2. Финализация в целевой статус (29, 30, 35, 48) с комментарием.
    3. Списание трудозатрат.
    4. Автоиндексация в pgvector RAG при закрытии.
    """
    task_ids = payload.task_ids
    if not task_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Список task_ids не может быть пустым.",
        )

    # Защитный механизм: Dead Man's Switch (Rate Limiter)
    if not payload.dry_run:
        try:
            await enforce_triage_apply_rate_limit(
                ticket_count=len(task_ids),
                confirmed_by_human=payload.confirmed_by_human,
            )
        except DeadMansSwitchError as e:
            logger.warning(
                "Аварийный тормоз (Dead Man's Switch) сработал на /triage/apply: %s",
                e,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=str(e),
            )

    status_id = payload.status_id
    comment = payload.comment
    expenses = payload.expenses
    executor_ids = payload.executor_ids or settings.DEFAULT_EXECUTOR_IDS
    dry_run = payload.dry_run

    results = []

    for tid in task_ids:
        if dry_run:
            results.append({
                "task_id": tid,
                "status": "simulated",
                "target_status_id": status_id,
            })
            continue

        # 1. Если статус не 27, сначала берем в работу
        if status_id != 27:
            await intraservice.update_task_full(
                auth_b64=service_auth_b64,
                task_id=tid,
                status_id=27,
                executor_ids=executor_ids,
            )

        # 2. Атомарное обновление в целевой статус
        upd_ok = await intraservice.update_task_full(
            auth_b64=service_auth_b64,
            task_id=tid,
            status_id=status_id,
            comment=comment if comment else None,
            executor_ids=executor_ids,
        )

        # 3. Списание трудозатрат
        exp_ok = True
        if expenses and expenses > 0:
            exp_ok = await intraservice.add_task_expenses(
                auth_b64=service_auth_b64,
                task_id=tid,
                minutes=expenses,
                user_id=settings.PRIMARY_EXECUTOR_ID,
            )

        # 4. Автообучение RAG при закрытии (29 или 30)
        # Для статуса 30 индексируем только если комментарий содержит реальное решение/инструкцию (не шаблонную заглушку)
        clean_comment = comment.strip() if comment else ""
        should_index_rag = False
        if status_id == 29 and clean_comment:
            should_index_rag = True
        elif status_id == 30 and len(clean_comment) >= 35:
            # Исключаем generic шаблоны отмены без полезной семантики
            if not clean_comment.startswith("Заявка переведена в статус Отменена"):
                should_index_rag = True

        if should_index_rag:
            try:
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
                        db=db,
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
                logger.error(
                    "Ошибка автоиндексации заявки #%d в RAG: %s", tid, e
                )

        results.append({
            "task_id": tid,
            "status": "success" if (upd_ok and exp_ok) else "partial_failure",
            "update_ok": upd_ok,
            "expenses_ok": exp_ok,
        })

    return {"results": results}


@router.get("/duplicates", status_code=status.HTTP_200_OK)
async def get_duplicates_in_queue(
    filter_id: int = Query(984, description="ID фильтра очереди"),
    limit: int = Query(10, description="Максимальное число дубликатов"),
    service_auth_b64: str = Depends(get_service_auth_b64),
):
    """
    Поиск и группировка заявок-дубликатов в очереди 1-й линии.
    """
    tasks = await intraservice.get_tasks_by_filter(
        auth_b64=service_auth_b64,
        filter_id=filter_id,
        page=1,
        page_size=max(limit * 5, 50),
    )
    active_tasks = [t for t in tasks if t.get("StatusId") not in (29, 30)]
    detector = DuplicateDetector()
    duplicates = detector.find_duplicates(active_tasks)
    return {"total": len(duplicates), "duplicates": duplicates[:limit]}


# ---------------------------------------------------------------------------
# Эндпоинты RAG и Семантического поиска
# ---------------------------------------------------------------------------


@router.post("/rag/search", status_code=status.HTTP_200_OK)
async def rag_search_endpoint(
    payload: RAGSearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Семантический поиск похожих решений в векторной базе PostgreSQL pgvector.
    """
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
    """
    Прямая индексация решения задачи в векторную базу PostgreSQL pgvector.
    """
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
    """
    Массовая фоновая синхронизация закрытых заявок из IntraService в векторную базу pgvector.
    """
    result = await sync_historical_closed_tasks(
        auth_b64=service_auth_b64,
        db=db,
        days=payload.days,
        limit=payload.limit,
    )
    return result


# ---------------------------------------------------------------------------
# Справочники каталога и сервисов
# ---------------------------------------------------------------------------


@router.get("/services", status_code=status.HTTP_200_OK)
async def get_root_services():
    """
    Возвращает список корневых разделов каталога услуг IntraService с номерами 01..16.
    """
    return [
        {"root_number": k, "id": v["id"], "name": v["name"]}
        for k, v in sorted(ROOT_SERVICES.items())
    ]


@router.get("/catalog", status_code=status.HTTP_200_OK)
async def get_full_catalog(
    search: str | None = Query(None, description="Поисковый фильтр"),
    service_auth_b64: str = Depends(get_service_auth_b64),
):
    """
    Возвращает полный каталог услуг IntraService с опциональным поиском.
    """
    catalog = await intraservice.get_services(service_auth_b64) or []
    if search:
        q = search.lower()
        catalog = [s for s in catalog if q in (s.get("Name") or "").lower()]
    return catalog


@router.get("/templates", status_code=status.HTTP_200_OK)
async def get_triage_templates():
    """
    Возвращает все доступные шаблоны ответов инженера.
    """
    return load_templates()


# ---------------------------------------------------------------------------
# Сессионное состояние оператора (пропуск заявок)
# ---------------------------------------------------------------------------


@router.post("/session/skip", status_code=status.HTTP_200_OK)
async def skip_session_tasks(
    payload: SkipSessionRequest,
    operator: str = Depends(verify_admin_or_api_key),
):
    """
    Помечает заявки как пропущенные в текущей смене оператора.
    """
    op = payload.operator_id or operator
    redis_key = f"session:{op}:skipped_task_ids" if op and op != "bot_or_cli" else "session:skipped_task_ids"

    try:
        r = get_redis_client()
        for tid in payload.task_ids:
            await r.sadd(redis_key, str(tid))
            await r.sadd("session:skipped_task_ids", str(tid))
        await r.expire(redis_key, settings.SKIPPED_TASKS_REDIS_TTL)
        return {
            "status": "success",
            "skipped_count": len(payload.task_ids),
            "operator": op,
        }
    except Exception as e:
        logger.exception("Ошибка сохранения %s: %s", redis_key, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка Redis: {e}",
        )


@router.post("/session/reset", status_code=status.HTTP_200_OK)
async def reset_session_tasks(
    operator_id: str | None = Query(None, description="Идентификатор оператора"),
    operator: str = Depends(verify_admin_or_api_key),
):
    """
    Сбрасывает сессионный кэш пропущенных заявок.
    """
    op = operator_id or operator
    try:
        r = get_redis_client()
        await r.delete("session:skipped_task_ids")
        if op:
            await r.delete(f"session:{op}:skipped_task_ids")
        return {"status": "success", "message": "Сессия сброшена", "operator": op}
    except Exception as e:
        logger.exception("Ошибка сброса сессии: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка Redis: {e}",
        )

