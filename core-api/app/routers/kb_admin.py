"""
Канонический роутер администрирования базы знаний RAG (Knowledge Base Admin).
Включает модерацию прецедентов, черный список (Blacklisting), статистику покрытия,
иерархическое дерево каталога услуг и прямую синхронизацию без внешних зомби-сервисов.
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import AsyncSessionLocal, TaskKnowledgeBase, get_db
from app.routers.admin_settings import require_admin_auth
from app.routers.deps import get_service_auth_b64
from app.services.rag import sync_historical_closed_tasks
from app.services.worker import get_redis_client

logger = logging.getLogger("intralink.kb_admin")

router = APIRouter(
    prefix="/api/v1/admin/kb",
    tags=["Knowledge Base Administration"],
    dependencies=[Depends(require_admin_auth)],
)


def build_service_tree(flat_services: list[dict]) -> list[dict]:
    """Сборка плоского списка услуг IntraService в иерархическое дерево."""
    nodes = {s["id"]: {**s, "children": []} for s in flat_services if "id" in s}
    tree = []
    for _s_id, node in nodes.items():
        parent_id = node.get("parent_id")
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            tree.append(node)
    return tree


class KBSyncRequest(BaseModel):
    days: int = Field(30, ge=1, le=365, description="Глубина сбора закрытых заявок в днях")
    limit: int = Field(100, ge=1, le=1000, description="Максимальное количество заявок для индексации")


class KBExampleItem(BaseModel):
    task_id: int
    original_name: str
    problem: str
    solution: str
    service_id: int
    service_name: str
    status_name: str
    root_cause: str | None = None
    root_id: str | None = None


class KBExamplesResponse(BaseModel):
    total: int
    page: int
    limit: int
    examples: list[KBExampleItem]


# ---------------------------------------------------------------------------
# 1. Дерево услуг каталога
# ---------------------------------------------------------------------------


@router.get("/services-tree", status_code=status.HTTP_200_OK)
async def get_services_tree() -> list[dict[str, Any]]:
    """
    Возвращает каталог услуг в виде иерархического дерева с дочерними элементами.
    Данные берутся из кэша Redis (при отсутствии выполняется автосинхронизация).
    """
    try:
        r = get_redis_client()
        catalog_str = await r.get("worker:service_catalog")

        if not catalog_str:
            from app.services.worker import sync_service_catalog

            await sync_service_catalog()
            catalog_str = await r.get("worker:service_catalog")

        if not catalog_str:
            return []

        if isinstance(catalog_str, bytes):
            catalog_str = catalog_str.decode("utf-8")

        flat_catalog = json.loads(catalog_str)
        return build_service_tree(flat_catalog)
    except Exception as e:
        logger.exception("Ошибка при построении дерева услуг: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось построить дерево услуг: {e}",
        )


# ---------------------------------------------------------------------------
# 2. Просмотр и пагинация базы знаний (Examples)
# ---------------------------------------------------------------------------


@router.get("/examples", response_model=KBExamplesResponse, status_code=status.HTTP_200_OK)
async def get_kb_examples(
    page: int = Query(1, ge=1, description="Номер страницы"),
    limit: int = Query(20, ge=1, le=100, description="Количество на страницу"),
    service_id: int | None = Query(None, description="Фильтр по ID конкретной услуги"),
    root_id: str | None = Query(None, description="Фильтр по корневому разделу каталога (01..16)"),
    search: str | None = Query(None, description="Текстовый поиск по проблеме или решению"),
    db: AsyncSession = Depends(get_db),
):
    """
    Просмотр проиндексированных прецедентов RAG (исключая черный список).
    Поддерживает пагинацию, фильтр по корневому разделу или конкретной услуге и полнотекстовый поиск.
    """
    try:
        offset = (page - 1) * limit
        query = select(TaskKnowledgeBase).where(TaskKnowledgeBase.is_blacklisted.is_(False))

        # Фильтр по корневому разделу (все дочерние service_id)
        if root_id:
            from app.services.rag import get_subservice_ids_for_root
            sub_ids = get_subservice_ids_for_root(root_id)
            if sub_ids:
                query = query.where(TaskKnowledgeBase.service_id.in_(sub_ids))
        elif service_id is not None:
            query = query.where(TaskKnowledgeBase.service_id == service_id)

        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.where(
                (TaskKnowledgeBase.problem.ilike(term))
                | (TaskKnowledgeBase.solution.ilike(term))
                | (TaskKnowledgeBase.original_name.ilike(term))
            )

        # Считаем общее число записей
        count_query = select(func.count(TaskKnowledgeBase.task_id)).where(
            TaskKnowledgeBase.is_blacklisted.is_(False)
        )
        if root_id:
            from app.services.rag import get_subservice_ids_for_root
            sub_ids = get_subservice_ids_for_root(root_id)
            if sub_ids:
                count_query = count_query.where(TaskKnowledgeBase.service_id.in_(sub_ids))
        elif service_id is not None:
            count_query = count_query.where(TaskKnowledgeBase.service_id == service_id)

        if search and search.strip():
            term = f"%{search.strip()}%"
            count_query = count_query.where(
                (TaskKnowledgeBase.problem.ilike(term))
                | (TaskKnowledgeBase.solution.ilike(term))
                | (TaskKnowledgeBase.original_name.ilike(term))
            )

        total = await db.scalar(count_query) or 0

        # Выборка страницы
        query = query.order_by(TaskKnowledgeBase.task_id.desc()).offset(offset).limit(limit)
        result = await db.execute(query)
        rows = result.scalars().all()

        # Подтягиваем названия разделов из кэша
        service_names_map: dict[int, str] = {}
        try:
            r = get_redis_client()
            cat_raw = await r.get("worker:service_catalog")
            if cat_raw:
                if isinstance(cat_raw, bytes):
                    cat_raw = cat_raw.decode("utf-8")
                flat = json.loads(cat_raw)
                service_names_map = {item["id"]: item.get("name", "") for item in flat if "id" in item}
        except Exception as e_redis:
            logger.debug("Не удалось получить каталог услуг для обогащения имен: %s", e_redis)

        examples: list[KBExampleItem] = []
        for r in rows:
            s_name = r.service_name or service_names_map.get(r.service_id, f"Услуга #{r.service_id}")
            c_data = r.classification_data or {}
            examples.append(
                KBExampleItem(
                    task_id=r.task_id,
                    original_name=r.original_name or "",
                    problem=r.problem or "",
                    solution=r.solution or "",
                    service_id=r.service_id or 0,
                    service_name=s_name,
                    status_name=r.status_name or "",
                    root_cause=c_data.get("root_cause"),
                    root_id=c_data.get("root_id"),
                )
            )

        return KBExamplesResponse(
            total=total,
            page=page,
            limit=limit,
            examples=examples,
        )
    except Exception as e:
        logger.exception("Ошибка при чтении базы знаний RAG: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера при чтении базы знаний: {e}",
        )


# ---------------------------------------------------------------------------
# 3. Модерация: Добавление в черный список (Blacklisting)
# ---------------------------------------------------------------------------


@router.delete("/examples/{task_id}", status_code=status.HTTP_200_OK)
async def delete_or_blacklist_kb_example(
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Модерация базы знаний: занесение задачи в черный список (Blacklist).
    Очищает эмбеддинг и текстовые поля, исключая повторные галлюцинации и ошибочные рекомендации.
    """
    try:
        query = select(TaskKnowledgeBase).where(TaskKnowledgeBase.task_id == task_id)
        result = await db.execute(query)
        example = result.scalar_one_or_none()

        if not example:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Заявка #{task_id} не найдена в базе знаний.",
            )

        example.is_blacklisted = True
        example.embedding = None
        example.problem = ""
        example.solution = ""

        await db.commit()
        logger.info("Задача #%d успешно скрыта из базы знаний RAG", task_id)
        return {
            "status": "success",
            "task_id": task_id,
            "message": f"Задача #{task_id} скрыта из базы знаний RAG.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ошибка при скрытии задачи #%d из базы знаний: %s", task_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось скрыть задачу из базы знаний: {e}",
        )


@router.delete("/purge", status_code=status.HTTP_200_OK)
async def purge_knowledge_base(
    db: AsyncSession = Depends(get_db),
):
    """
    Полная очистка базы знаний RAG (удаление всех векторов и прецедентов из PostgreSQL
    и сброс кэша эмбеддингов в Redis).
    """
    try:
        from sqlalchemy import delete
        result = await db.execute(delete(TaskKnowledgeBase))
        deleted_count = result.rowcount
        await db.commit()

        # Очистка кэша RAG и сброс состояния синхронизации в Redis
        try:
            redis = get_redis_client()
            keys = await redis.keys("rag:emb:*")
            if keys:
                await redis.delete(*keys)
            await redis.delete("lock:kb_sync")
            await redis.delete("kb:sync_progress")
        except Exception as re:
            logger.warning("Не удалось сбросить кэш эмбеддингов/прогресс в Redis: %s", re)

        logger.info("База знаний RAG успешно очищена. Удалено записей: %d", deleted_count)
        return {
            "status": "success",
            "deleted": deleted_count,
            "message": f"База знаний RAG успешно очищена. Удалено записей: {deleted_count}.",
        }
    except Exception as e:
        logger.exception("Ошибка при полной очистке базы знаний RAG: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Не удалось очистить базу знаний: {e}",
        )



# ---------------------------------------------------------------------------
# 4. Статистика покрытия базы знаний
# ---------------------------------------------------------------------------


@router.get("/stats", status_code=status.HTTP_200_OK)
async def get_kb_statistics(
    db: AsyncSession = Depends(get_db),
    admin_payload: dict = Depends(require_admin_auth),
) -> dict[str, Any]:
    """
    Возвращает матрицу покрытия базы знаний: количество прецедентов
    в разрезе услуг и статусов закрытия (исключая черный список),
    а также статус готовности учетных данных для синхронизации.
    """
    try:
        query = (
            select(
                TaskKnowledgeBase.service_id,
                TaskKnowledgeBase.status_name,
                func.count(TaskKnowledgeBase.task_id),
            )
            .where(TaskKnowledgeBase.is_blacklisted.is_(False))
            .group_by(TaskKnowledgeBase.service_id, TaskKnowledgeBase.status_name)
        )

        result = await db.execute(query)
        rows = result.all()

        services_stats: dict[str, dict[str, Any]] = {}
        total_examples = 0

        for s_id, status_name, cnt in rows:
            s_key = str(s_id)
            if s_key not in services_stats:
                services_stats[s_key] = {"total": 0, "by_status": {}}
            services_stats[s_key]["by_status"][status_name or "Без статуса"] = cnt
            services_stats[s_key]["total"] += cnt
            total_examples += cnt

        # Получаем количество заблокированных (Blacklisted)
        blacklisted_query = select(func.count(TaskKnowledgeBase.task_id)).where(
            TaskKnowledgeBase.is_blacklisted.is_(True)
        )
        blacklisted_count = await db.scalar(blacklisted_query) or 0

        # Превентивная проверка доступности учетных данных для синхронизации
        readiness = {
            "ready": False,
            "auth_source": "none",
            "account_name": None,
            "message": "Учетные данные IntraService не настроены. Для синхронизации войдите под учетной записью IntraService или настройте сервисный аккаунт в Хранилище.",
        }

        username = admin_payload.get("sub") if isinstance(admin_payload, dict) else None
        redis = get_redis_client()
        if username:
            try:
                op_auth = await redis.get(f"admin_auth:{username}")
                if op_auth:
                    readiness = {
                        "ready": True,
                        "auth_source": "operator_session",
                        "account_name": str(username),
                        "message": f"Синхронизация готова: используется активная сессия оператора '{username}'",
                    }
            except Exception:
                pass

        if not readiness["ready"]:
            if settings.INTRASERVICE_SERVICE_LOGIN and settings.INTRASERVICE_SERVICE_PASSWORD:
                readiness = {
                    "ready": True,
                    "auth_source": "service_account",
                    "account_name": str(settings.INTRASERVICE_SERVICE_LOGIN),
                    "message": f"Синхронизация готова: используется системный аккаунт '{settings.INTRASERVICE_SERVICE_LOGIN}'",
                }
            else:
                try:
                    svc_auth = await redis.get("worker:service_auth_b64")
                    if svc_auth:
                        readiness = {
                            "ready": True,
                            "auth_source": "service_account",
                            "account_name": "Vault Service Account",
                            "message": "Синхронизация готова: настроен сервисный аккаунт в Хранилище (Vault)",
                        }
                except Exception:
                    pass

        from app.services.rag import get_all_root_services, get_subservice_ids_for_root, check_embedding_health
        roots = get_all_root_services()

        root_counts: dict[str, int] = {}
        for r in roots:
            sids = get_subservice_ids_for_root(r["root_id"])
            cnt = sum(services_stats.get(str(s), {}).get("total", 0) for s in sids)
            root_counts[r["root_id"]] = cnt

        embed_ok, embed_msg = await check_embedding_health()

        return {
            "total_active_examples": total_examples,
            "total_blacklisted_examples": blacklisted_count,
            "services_count": len(services_stats),
            "services": services_stats,
            "sync_readiness": readiness,
            "embedding_readiness": {
                "ready": embed_ok,
                "message": embed_msg,
                "model": getattr(settings, "EMBEDDING_MODEL", "gemini-embedding-001"),
                "dimension": getattr(settings, "EMBEDDING_DIMENSION", 3072),
            },
            "root_services": roots,
            "root_counts": root_counts,
        }
    except Exception as e:
        logger.exception("Ошибка при сборе статистики базы знаний: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при сборе статистики базы знаний: {e}",
        )


# ---------------------------------------------------------------------------
# 5. Прямая и умная стратифицированная синхронизация базы знаний
# ---------------------------------------------------------------------------


@router.post("/sync", status_code=status.HTTP_200_OK)
async def trigger_kb_sync(
    payload: KBSyncRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
    db: AsyncSession = Depends(get_db),
):
    """
    Запуск прямой синхронизации закрытых заявок из IntraService в векторную базу pgvector.
    Работает in-process в Core API без ожидания внешних сервисов.
    """
    # Pre-flight Check работоспособности сервиса эмбеддингов
    from app.services.rag import check_embedding_health
    embed_ok, embed_msg = await check_embedding_health()
    if not embed_ok:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=f"Служба генерации эмбеддингов недоступна ({embed_msg}). Синхронизация отменена во избежание холостого прогона.",
        )

    try:
        result = await sync_historical_closed_tasks(
            auth_b64=service_auth_b64,
            db=db,
            days=payload.days,
            limit=payload.limit,
        )
        return {
            "status": "success",
            "message": f"Синхронизация базы знаний завершена за последние {payload.days} дней.",
            "details": result,
        }
    except Exception as e:
        logger.exception("Ошибка при выполнении синхронизации базы знаний: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка синхронизации базы знаний: {e}",
        )


class KBStratifiedSyncRequest(BaseModel):
    quota_per_service: int = Field(30, ge=5, le=100, description="Квота качественных прецедентов на раздел")
    days: int = Field(60, ge=7, le=365, description="Глубина выборки в днях")
    root_id: str | None = Field(None, description="ID конкретного корневого раздела (например '03') или None для всех")


@router.post("/sync-stratified", status_code=status.HTTP_202_ACCEPTED)
async def trigger_stratified_kb_sync(
    payload: KBStratifiedSyncRequest,
    service_auth_b64: str = Depends(get_service_auth_b64),
):
    """
    Асинхронный запуск фонового умного наполнения RAG по корневым разделам (01..17).
    """
    # Pre-flight Check работоспособности сервиса эмбеддингов
    from app.services.rag import check_embedding_health
    embed_ok, embed_msg = await check_embedding_health()
    if not embed_ok:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=f"Служба генерации эмбеддингов недоступна ({embed_msg}). Синхронизация отменена во избежание холостого прогона.",
        )

    redis = get_redis_client()
    lock = await redis.get("lock:kb_sync")
    if lock:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Синхронизация базы знаний уже выполняется. Дождитесь завершения текущего процесса.",
        )

    from app.services.rag import sync_stratified_kb

    asyncio.create_task(
        sync_stratified_kb(
            auth_b64=service_auth_b64,
            quota_per_service=payload.quota_per_service,
            days=payload.days,
            target_root_id=payload.root_id,
        )
    )

    return {
        "status": "started",
        "message": "Умная фоновая синхронизация базы знаний успешно запущена.",
        "quota_per_service": payload.quota_per_service,
        "days": payload.days,
        "root_id": payload.root_id,
    }


@router.get("/sync-status", status_code=status.HTTP_200_OK)
async def get_kb_sync_status_endpoint() -> dict[str, Any]:
    """
    Возвращает актуальный статус и прогресс умной синхронизации базы знаний из Redis.
    """
    from app.services.rag import get_kb_sync_progress

    progress = await get_kb_sync_progress()
    return progress

