"""
Сервис RAG (Retrieval-Augmented Generation) и семантического поиска в PostgreSQL pgvector.
"""

import logging
import re
from typing import Any
import aiohttp
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import TaskKnowledgeBase
from app.services import intraservice

logger = logging.getLogger("core_api.rag")

_fastembed_model = None


def get_local_embed_model():
    """Ленивая загрузка локальной модели fastembed."""
    global _fastembed_model
    if _fastembed_model is None:
        try:
            from fastembed import TextEmbedding

            _fastembed_model = TextEmbedding(
                model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            )
        except Exception as e:
            logger.debug("Ошибка инициализации fastembed: %s", e)
    return _fastembed_model


def clean_html(raw_html: str | None) -> str:
    """Удаляет HTML-теги из текста."""
    if not raw_html:
        return ""
    cleanr = re.compile(r"<[^>]+>")
    text_clean = re.sub(cleanr, " ", raw_html)
    return " ".join(text_clean.split()).strip()


async def get_embedding_vector(text_input: str) -> list[float] | None:
    """
    Генерирует вектор эмбеддинга заданной размерности (3072 dim через LiteLLM Proxy / Gemini
    или fallback на FastEmbed).
    """
    clean_text = clean_html(text_input).strip()[:4000]
    if not clean_text:
        return None

    # 1. Попытка через LiteLLM Proxy
    if settings.LITELLM_BASE_URL:
        try:
            url = f"{settings.LITELLM_BASE_URL.rstrip('/')}/embeddings"
            headers = {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"}
            payload = {
                "input": [clean_text],
                "model": settings.EMBEDDING_MODEL,
            }
            timeout = aiohttp.ClientTimeout(total=4.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, headers=headers, json=payload
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vec = data.get("data", [{}])[0].get("embedding")
                        if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                            return vec
        except Exception:
            pass

    # 2. Попытка через Gemini API (если передан GEMINI_API_KEY)
    if getattr(settings, "GEMINI_API_KEY", None):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={settings.GEMINI_API_KEY}"
            payload = {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": clean_text}]},
            }
            timeout = aiohttp.ClientTimeout(total=4.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vec = data.get("embedding", {}).get("values", [])
                        if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                            return vec
        except Exception:
            pass

    return None


async def search_knowledge_base(
    db: AsyncSession,
    query_text: str,
    limit: int = 3,
    distance_threshold: float = 0.70,
) -> list[dict[str, Any]]:
    """
    Выполняет косинусный семантический поиск похожих решений в базе знаний pgvector.
    """
    clean_query = clean_html(query_text).strip()
    if not clean_query:
        return []

    query_vector = await get_embedding_vector(clean_query)
    if not query_vector:
        logger.debug(
            "Не удалось сформировать вектор для поискового запроса: '%s'",
            clean_query[:50],
        )
        return []

    try:
        # Cosine distance operator <=>
        stmt = (
            select(
                TaskKnowledgeBase.task_id,
                TaskKnowledgeBase.original_name,
                TaskKnowledgeBase.problem,
                TaskKnowledgeBase.solution,
                TaskKnowledgeBase.service_id,
                TaskKnowledgeBase.service_name,
                TaskKnowledgeBase.status_name,
                TaskKnowledgeBase.classification_data,
                TaskKnowledgeBase.embedding.cosine_distance(query_vector).label(
                    "distance"
                ),
            )
            .where(
                TaskKnowledgeBase.embedding.is_not(None),
                TaskKnowledgeBase.is_blacklisted.is_(False),
            )
            .order_by("distance")
            .limit(limit)
        )

        result = await db.execute(stmt)
        rows = result.all()

        matches = []
        for r in rows:
            dist = float(r.distance) if r.distance is not None else 1.0
            if dist <= distance_threshold:
                sim_pct = round(max(0.0, min(99.0, (1.0 - dist) * 100.0)), 1)
                matches.append({
                    "task_id": r.task_id,
                    "name": r.original_name,
                    "problem": r.problem,
                    "solution": r.solution,
                    "service_id": r.service_id,
                    "service_name": r.service_name,
                    "status_name": r.status_name,
                    "similarity_pct": sim_pct,
                    "distance": round(dist, 4),
                    "storage_tier": "PostgreSQL (pgvector)",
                })
        return matches
    except Exception as e:
        logger.exception("Ошибка семантического поиска в pgvector: %s", e)
        return []


async def index_task_knowledge(
    db: AsyncSession,
    task_id: int,
    original_name: str,
    problem: str,
    solution: str,
    service_id: int,
    service_name: str,
    status_name: str,
    classification_data: dict[str, Any] | None = None,
) -> bool:
    """
    Индексирует решение заявки в таблицу task_knowledge_base с генерацией эмбеддинга.
    """
    try:
        embed_input = (
            f"Тема: {original_name}\nПроблема: {problem}\nРешение: {solution}"
        )
        vec = await get_embedding_vector(embed_input)

        stmt = select(TaskKnowledgeBase).where(
            TaskKnowledgeBase.task_id == task_id
        )
        res = await db.execute(stmt)
        existing = res.scalar_one_or_none()

        if existing:
            existing.original_name = original_name
            existing.problem = problem
            existing.solution = solution
            existing.service_id = service_id
            existing.service_name = service_name
            existing.status_name = status_name
            existing.classification_data = classification_data or {}
            existing.embedding = vec
            existing.is_blacklisted = False
        else:
            item = TaskKnowledgeBase(
                task_id=task_id,
                original_name=original_name,
                problem=problem,
                solution=solution,
                service_id=service_id,
                service_name=service_name,
                status_name=status_name,
                classification_data=classification_data or {},
                embedding=vec,
                is_blacklisted=False,
            )
            db.add(item)

        await db.commit()
        logger.info(
            "Заявка #%d успешно проиндексирована в базе знаний RAG", task_id
        )
        return True
    except Exception as e:
        logger.exception("Ошибка индексации заявки #%d в RAG: %s", task_id, e)
        await db.rollback()
        return False


async def sync_historical_closed_tasks(
    auth_b64: str,
    db: AsyncSession,
    days: int = 30,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Выгружает закрытые заявки из IntraService (StatusId in 29, 30),
    извлекает финальный комментарий инженера и сохраняет их в векторную базу pgvector.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M")

    params = {
        "ChangedMoreThan": cutoff_str,
        "pagesize": str(min(limit * 2, 100)),
        "page": "1",
        "include": "status,service",
    }

    raw_tasks = await intraservice.get_tasks(auth_b64=auth_b64, filters=params)
    tasks_list = []
    if isinstance(raw_tasks, dict):
        tasks_list = raw_tasks.get("Tasks", [])
    elif isinstance(raw_tasks, list):
        tasks_list = raw_tasks

    # Фильтруем закрытые заявки (29: Выполнена, 30: Отменена)
    closed = [t for t in tasks_list if t.get("StatusId") in (29, 30)]
    indexed_count = 0
    skipped_count = 0

    for t in closed[:limit]:
        tid = t.get("Id")
        if not tid:
            continue

        # Проверяем, есть ли уже в базе
        stmt = select(TaskKnowledgeBase.task_id).where(
            TaskKnowledgeBase.task_id == tid
        )
        res = await db.execute(stmt)
        if res.scalar_one_or_none():
            skipped_count += 1
            continue

        # Получаем историю переписки для нахождения финального решения
        lifetime = await intraservice.get_task_lifetime(auth_b64, tid) or []
        solution_text = ""
        for item in reversed(lifetime):
            comm = (item.get("Comment") or "").strip()
            if comm and len(comm) > 10:
                solution_text = comm
                break

        if not solution_text:
            skipped_count += 1
            continue

        t_name = t.get("Name") or f"Заявка #{tid}"
        t_desc = t.get("Description") or ""
        s_id = t.get("ServiceId") or 0
        s_name = t.get("ServiceName") or "Общие"
        st_name = t.get("StatusName") or "Закрыта"

        ok = await index_task_knowledge(
            db=db,
            task_id=tid,
            original_name=t_name,
            problem=f"{t_name}. {t_desc}".strip(),
            solution=solution_text,
            service_id=s_id,
            service_name=s_name,
            status_name=st_name,
            classification_data={"synced_from_history": True, "days": days},
        )
        if ok:
            indexed_count += 1
        else:
            skipped_count += 1

    return {
        "status": "success",
        "total_fetched": len(tasks_list),
        "total_closed": len(closed),
        "indexed": indexed_count,
        "skipped": skipped_count,
    }

