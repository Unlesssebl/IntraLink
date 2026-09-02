import asyncio
import logging
import re
from typing import Any
import aiohttp
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import TaskKnowledgeBase
from app.services import intraservice
from app.services.ai import DataCircuit, RoutingMetadata, data_sanitizer

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


def _get_fastembed_vector_sync(text_input: str) -> list[float] | None:
    """Синхронный расчет эмбеддинга FastEmbed в отдельном потоке (worker thread)."""
    model = get_local_embed_model()
    if not model:
        return None
    try:
        embeddings = list(model.embed([text_input]))
        if embeddings:
            vec = embeddings[0].tolist()
            return vec
    except Exception as e:
        logger.debug("Ошибка генерации FastEmbed вектора: %s", e)
    return None


def clean_html(raw_html: str | None) -> str:
    """Удаляет HTML-теги из текста."""
    if not raw_html:
        return ""
    cleanr = re.compile(r"<[^>]+>")
    text_clean = re.sub(cleanr, " ", raw_html)
    return " ".join(text_clean.split()).strip()


_rag_session: aiohttp.ClientSession | None = None


async def get_rag_http_session() -> aiohttp.ClientSession:
    """Возвращает переиспользуемую HTTP-сессию для векторизации."""
    global _rag_session
    if _rag_session is None or _rag_session.closed:
        timeout = aiohttp.ClientTimeout(total=4.0)
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
        _rag_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _rag_session


async def close_rag_session() -> None:
    """Закрывает HTTP-сессию векторизации."""
    global _rag_session
    if _rag_session and not _rag_session.closed:
        await _rag_session.close()
        _rag_session = None


async def get_embedding_vector(
    text_input: str,
    force_local: bool = False,
    circuit: DataCircuit | None = None,
) -> list[float] | None:
    """
    Генерирует вектор эмбеддинга заданной размерности (3072 dim):
    - RED (Закрытый контур / force_local): строго локальные эмбеддеры (Ollama / FastEmbed) без отправки наружу.
    - YELLOW (Трансформируемый): автоматическая десенсибилизация перед вызовом Cloud LiteLLM / Gemini API.
    - GREEN (Открытый): прямой вызов Cloud LiteLLM / Gemini с fallback на локальные модели.
    """
    clean_text = clean_html(text_input).strip()[:4000]
    if not clean_text:
        return None

    # 1. Если принудительно локальный режим или RED контур -> строго локальные эмбеддеры
    if force_local or circuit == DataCircuit.RED:
        # Попытка через локальный сервис Ollama (/api/embed)
        if getattr(settings, "OLLAMA_BASE_URL", None):
            try:
                session = await get_rag_http_session()
                ollama_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/embed"
                payload = {
                    "model": getattr(settings, "OLLAMA_EMBEDDING_MODEL", "bge-m3"),
                    "input": clean_text,
                }
                async with session.post(
                    ollama_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=4.0),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        embeddings = data.get("embeddings")
                        if embeddings and len(embeddings) > 0:
                            vec = embeddings[0]
                            if len(vec) == settings.EMBEDDING_DIMENSION:
                                return vec
            except Exception:
                pass

        # Fallback: локальный FastEmbed в отдельном пуле потоков
        try:
            vec = await asyncio.to_thread(_get_fastembed_vector_sync, clean_text)
            if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                return vec
            if vec and len(vec) != settings.EMBEDDING_DIMENSION:
                logger.warning(
                    "Размерность вектора FastEmbed (%d) не совпадает с EMBEDDING_DIMENSION (%d).",
                    len(vec),
                    settings.EMBEDDING_DIMENSION,
                )
        except Exception as e:
            logger.debug("Ошибка генерации вектора FastEmbed: %s", e)
        return None

    # 2. Подготовка текста для облачных эмбеддеров (маскирование PII при YELLOW или если обнаружены сущности)
    cloud_payload_text = clean_text
    if circuit == DataCircuit.YELLOW or circuit is None:
        san_res = data_sanitizer.sanitize(clean_text)
        if san_res.detected_types:
            cloud_payload_text = san_res.sanitized_text

    session = await get_rag_http_session()

    # 3. Попытка через LiteLLM Proxy
    if settings.LITELLM_BASE_URL:
        try:
            url = f"{settings.LITELLM_BASE_URL.rstrip('/')}/embeddings"
            headers = {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"}
            payload = {
                "input": [cloud_payload_text],
                "model": settings.EMBEDDING_MODEL,
            }
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    vec = data.get("data", [{}])[0].get("embedding")
                    if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                        return vec
        except Exception:
            pass

    # 4. Попытка через Gemini API (если передан GEMINI_API_KEY)
    if getattr(settings, "GEMINI_API_KEY", None):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={settings.GEMINI_API_KEY}"
            payload = {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": cloud_payload_text}]},
            }
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    vec = data.get("embedding", {}).get("values", [])
                    if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                        return vec
        except Exception:
            pass

    # 5. Локальный Fallback (Ollama / FastEmbed)
    if getattr(settings, "OLLAMA_BASE_URL", None):
        try:
            ollama_url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/embed"
            payload = {
                "model": getattr(settings, "OLLAMA_EMBEDDING_MODEL", "bge-m3"),
                "input": clean_text,
            }
            async with session.post(
                ollama_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=4.0),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    embeddings = data.get("embeddings")
                    if embeddings and len(embeddings) > 0:
                        vec = embeddings[0]
                        if len(vec) == settings.EMBEDDING_DIMENSION:
                            return vec
        except Exception:
            pass

    try:
        vec = await asyncio.to_thread(_get_fastembed_vector_sync, clean_text)
        if vec:
            if len(vec) == settings.EMBEDDING_DIMENSION:
                return vec
            logger.warning(
                "Размерность вектора FastEmbed (%d) не совпадает с EMBEDDING_DIMENSION (%d). Пропуск.",
                len(vec),
                settings.EMBEDDING_DIMENSION,
            )
    except Exception as e:
        logger.debug("Ошибка генерации вектора FastEmbed: %s", e)

    return None


async def search_knowledge_base(
    db: AsyncSession,
    query_text: str,
    limit: int = 3,
    distance_threshold: float = 0.70,
    circuit: DataCircuit | None = None,
    metadata: RoutingMetadata | None = None,
) -> list[dict[str, Any]]:
    """
    Выполняет косинусный семантический поиск похожих решений в базе знаний pgvector.
    Автоматически определяет контур безопасности поискового запроса.
    """
    clean_query = clean_html(query_text).strip()
    if not clean_query:
        return []

    # Оцениваем контур запроса, если не задан явно
    eval_circuit = circuit
    if eval_circuit is None:
        dec = data_sanitizer.evaluate_circuit(
            prompt=clean_query, metadata=metadata or RoutingMetadata()
        )
        eval_circuit = dec.circuit

    query_vector = await get_embedding_vector(clean_query, circuit=eval_circuit)
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
                    "circuit_evaluated": eval_circuit.value if eval_circuit else "green",
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
    force_local: bool = False,
    circuit: DataCircuit | None = None,
) -> bool:
    """
    Индексирует решение заявки в таблицу task_knowledge_base с генерацией эмбеддинга.
    Автоматически переключается на локальный контур при наличии паролей или конфиденциальности.
    """
    try:
        embed_input = (
            f"Тема: {original_name}\nПроблема: {problem}\nРешение: {solution}"
        )

        # Автоматическая оценка контура при индексации
        eval_circuit = circuit
        if eval_circuit is None and not force_local:
            eval_dec = data_sanitizer.evaluate_circuit(
                prompt=embed_input,
                metadata=RoutingMetadata(service_id=service_id),
            )
            eval_circuit = eval_dec.circuit

        vec = await get_embedding_vector(
            embed_input, force_local=force_local, circuit=eval_circuit
        )

        stmt = select(TaskKnowledgeBase).where(
            TaskKnowledgeBase.task_id == task_id
        )
        res = await db.execute(stmt)
        existing = res.scalar_one_or_none()

        merged_data = dict(classification_data or {})
        if eval_circuit:
            merged_data["circuit"] = eval_circuit.value

        if existing:
            existing.original_name = original_name
            existing.problem = problem
            existing.solution = solution
            existing.service_id = service_id
            existing.service_name = service_name
            existing.status_name = status_name
            existing.classification_data = merged_data
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
                classification_data=merged_data,
                embedding=vec,
                is_blacklisted=False,
            )
            db.add(item)

        await db.commit()
        logger.info(
            "Заявка #%d успешно проиндексирована в базе знаний RAG (контур: %s)",
            task_id,
            eval_circuit.value if eval_circuit else "default",
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

