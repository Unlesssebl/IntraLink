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
_fastembed_reranker = None


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


def get_local_reranker_model():
    """Ленивая загрузка локальной модели cross-encoder fastembed."""
    global _fastembed_reranker
    if _fastembed_reranker is None:
        try:
            from fastembed import TextCrossEncoder

            _fastembed_reranker = TextCrossEncoder(
                model_name="BAAI/bge-reranker-base"
            )
        except Exception as e:
            logger.debug(
                "Ошибка инициализации FastEmbed TextCrossEncoder: %s", e
            )
    return _fastembed_reranker


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


def _rerank_fastembed_sync(
    query: str, documents: list[str]
) -> list[float] | None:
    """Синхронная оценка релевантности пар (query, document) через Cross-Encoder."""
    model = get_local_reranker_model()
    if not model:
        return None
    try:
        scores = list(model.rerank(query, documents))
        return [float(s) for s in scores]
    except Exception as e:
        logger.debug("Ошибка выполнения FastEmbed rerank: %s", e)
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
            embed_model = getattr(settings, "EMBEDDING_MODEL", "gemini-embedding-2")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{embed_model}:embedContent?key={settings.GEMINI_API_KEY}"
            payload = {
                "model": f"models/{embed_model}",
                "content": {"parts": [{"text": cloud_payload_text}]},
            }
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    vec = data.get("embedding", {}).get("values", [])
                    if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                        return vec
        except Exception as e:
            logger.debug("Ошибка генерации Gemini эмбеддинга: %s", e)

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


# ---------------------------------------------------------------------------
# Query Distillation (AI & Rule-based отсечение шума)
# ---------------------------------------------------------------------------

_EMOTIONAL_NOISE_RE = re.compile(
    r"(?i)\b(?:здравствуйте|добрый день|доброе утро|добрый вечер|приветствую|привет|"
    r"пожалуйста|плиз|спс|спасибо|благодарю|срочно|asap|sos|караул|помогите|спасите|умоляю|"
    r"шеф ругается|начальник ругается|все пропало|всё пропало|ничего не работает|горит|паника|в панике|"
    r"очень нужно|срочная заявка|прошу помочь|помогите разобраться|подскажите|посодействуйте)\b",
)

_TECHNICAL_TOKENS_RE = re.compile(
    r"\b(?:0x[0-9a-fA-F]{4,8}|"
    r"[A-Za-zА-Яа-я\-_]{2,6}\d{3,5}|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"WLAN-WORKNET|Spooler|1C[:\s]Enterprise|1[СC]|WinRM|SMB|RPC|RDP|VPN|Outlook|Thunderbird|"
    r"(?:HP|Kyocera|Canon|Pantum|Xerox|Epson|Brother|Samsung)\s+[a-zA-Z0-9\-_]+)\b",
    re.IGNORECASE,
)

_RUSSIAN_STOPWORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все", "она",
    "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по", "только", "ее",
    "мне", "было", "вот", "от", "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда",
    "даже", "ну", "вдруг", "ли", "если", "уже", "или", "ни", "быть", "был", "него", "до",
    "вас", "нибудь", "опять", "уж", "вам", "ведь", "там", "потом", "себя", "ничего", "ей",
    "может", "они", "тут", "где", "есть", "надо", "ней", "для", "мы", "тебя", "их", "чем",
    "была", "сам", "чтоб", "без", "будто", "чего", "раз", "тоже", "себе", "под", "будет",
    "ж", "тогда", "кто", "этот", "того", "потому", "этого", "какой", "совсем", "ним", "здесь",
    "этом", "один", "почти", "мой", "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем",
    "всех", "никогда", "можно", "при", "наконец", "два", "об", "другой", "хоть", "после",
    "над", "больше", "тот", "через", "эти", "нас", "про", "всего", "них", "какая", "много",
    "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой", "перед", "иногда",
    "лучше", "чуть", "том", "нельзя", "такой", "им", "более", "всегда", "кстати", "очень",
}


def distill_search_query(
    raw_text: str, circuit: DataCircuit | None = None
) -> str:
    """
    Нормализует поисковый запрос (Query Distillation):
    - Отсекает эмоциональный шум, панику и приветствия.
    - Выделяет и сохраняет технические инварианты (коды ошибок 0x..., модели оборудования, имена служб).
    - Формирует емкий технический запрос для векторного и полнотекстового поиска.
    """
    clean_text = clean_html(raw_text).strip()
    if not clean_text:
        return ""

    # 1. Удаляем эмоциональный шум и приветствия
    without_noise = _EMOTIONAL_NOISE_RE.sub(" ", clean_text)

    # 2. Нормализуем пробелы и знаки препинания
    cleaned = re.sub(r"[\s\t\r\n]+", " ", without_noise).strip()
    cleaned = re.sub(r"^[\s,.;:!\-?]+|[\s,.;:!\-?]+$", "", cleaned).strip()

    # 3. Если после очистки остался осмысленный текст — возвращаем его
    if len(cleaned) >= 5:
        return cleaned

    # 4. Fallback: если весь текст был отфильтрован, извлекаем технические токены из оригинала
    tech_tokens = _TECHNICAL_TOKENS_RE.findall(clean_text)
    if tech_tokens:
        return " ".join(dict.fromkeys(tech_tokens))

    return clean_text


async def dense_vector_search(
    db: AsyncSession,
    query_text: str,
    limit: int = 10,
    distance_threshold: float = 0.85,
    circuit: DataCircuit | None = None,
    metadata: RoutingMetadata | None = None,
) -> list[dict[str, Any]]:
    """
    Векторный dense-поиск по косинусному расстоянию в pgvector.
    """
    clean_query = clean_html(query_text).strip()
    if not clean_query:
        return []

    eval_circuit = circuit
    if eval_circuit is None:
        dec = data_sanitizer.evaluate_circuit(
            prompt=clean_query, metadata=metadata or RoutingMetadata()
        )
        eval_circuit = dec.circuit

    query_vector = await get_embedding_vector(clean_query, circuit=eval_circuit)
    if not query_vector:
        return []

    try:
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
        for rank, r in enumerate(rows, start=1):
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
                    "classification_data": r.classification_data,
                    "similarity_pct": sim_pct,
                    "distance": round(dist, 4),
                    "rank": rank,
                    "storage_tier": "PostgreSQL (pgvector)",
                    "circuit_evaluated": eval_circuit.value if eval_circuit else "green",
                })
        return matches
    except Exception as e:
        logger.debug("Ошибка dense_vector_search: %s", e)
        return []


async def sparse_text_search(
    db: AsyncSession,
    query_text: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Полнотекстовый sparse-поиск по ключевым словам и техническим идентификаторам в базе решений.
    """
    from sqlalchemy import or_

    clean_query = clean_html(query_text).strip()
    if not clean_query:
        return []

    raw_tokens = re.findall(r"[\w0-9xX\-_]+", clean_query)
    tokens = [
        t.lower()
        for t in raw_tokens
        if len(t) >= 2 and t.lower() not in _RUSSIAN_STOPWORDS
    ]

    if not tokens:
        tokens = [t.lower() for t in raw_tokens if len(t) >= 2]
    if not tokens:
        return []

    try:
        conditions = []
        for t in tokens[:6]:
            conditions.append(TaskKnowledgeBase.problem.ilike(f"%{t}%"))
            conditions.append(TaskKnowledgeBase.original_name.ilike(f"%{t}%"))
            conditions.append(TaskKnowledgeBase.solution.ilike(f"%{t}%"))

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
            )
            .where(
                TaskKnowledgeBase.is_blacklisted.is_(False),
                or_(*conditions),
            )
            .limit(limit * 3)
        )

        result = await db.execute(stmt)
        rows = result.all()

        scored_matches = []
        for r in rows:
            prob_lower = (r.problem or "").lower()
            name_lower = (r.original_name or "").lower()
            sol_lower = (r.solution or "").lower()
            combined = f"{name_lower} {prob_lower} {sol_lower}"

            score = 0.0
            for t in tokens:
                if t in combined:
                    score += 1.0
                    if t in name_lower:
                        score += 1.5
                    if t.startswith("0x"):
                        score += 3.0

            if clean_query.lower() in combined:
                score += 5.0

            scored_matches.append({
                "task_id": r.task_id,
                "name": r.original_name,
                "problem": r.problem,
                "solution": r.solution,
                "service_id": r.service_id,
                "service_name": r.service_name,
                "status_name": r.status_name,
                "classification_data": r.classification_data,
                "sparse_score": score,
            })

        scored_matches.sort(key=lambda x: x["sparse_score"], reverse=True)
        top_matches = scored_matches[:limit]

        for idx, m in enumerate(top_matches, start=1):
            m["rank"] = idx

        return top_matches
    except Exception as e:
        logger.debug("Ошибка sparse_text_search: %s", e)
        return []


def reciprocal_rank_fusion(
    dense_results: list[dict[str, Any]],
    sparse_results: list[dict[str, Any]],
    k: int = 60,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """
    Слияние результатов Dense pgvector и Sparse tsvector по алгоритму Reciprocal Rank Fusion (RRF).
    Формула: RRF_Score = sum(1.0 / (k + rank_i))
    """
    scores: dict[int, float] = {}
    doc_map: dict[int, dict[str, Any]] = {}
    dense_ranks: dict[int, int] = {}
    sparse_ranks: dict[int, int] = {}

    for rank, doc in enumerate(dense_results, start=1):
        tid = doc["task_id"]
        dense_ranks[tid] = rank
        scores[tid] = scores.get(tid, 0.0) + (1.0 / (k + rank))
        if tid not in doc_map:
            doc_map[tid] = dict(doc)

    for rank, doc in enumerate(sparse_results, start=1):
        tid = doc["task_id"]
        sparse_ranks[tid] = rank
        scores[tid] = scores.get(tid, 0.0) + (1.0 / (k + rank))
        if tid not in doc_map:
            doc_map[tid] = dict(doc)

    sorted_tids = sorted(
        scores.keys(), key=lambda x: scores[x], reverse=True
    )[:limit]

    fused = []
    for tid in sorted_tids:
        item = doc_map[tid]
        rrf_score = scores[tid]

        sim_pct = round(
            min(99.0, max(15.0, (rrf_score / (2.0 / (k + 1))) * 100.0)), 1
        )
        if "distance" in item and item["distance"] is not None:
            dense_pct = round((1.0 - float(item["distance"])) * 100.0, 1)
            sim_pct = max(sim_pct, dense_pct)

        item["similarity_pct"] = sim_pct
        item["rrf_score"] = round(rrf_score, 6)
        item["dense_rank"] = dense_ranks.get(tid)
        item["sparse_rank"] = sparse_ranks.get(tid)
        item["search_type"] = (
            "hybrid_rrf"
            if (tid in dense_ranks and tid in sparse_ranks)
            else (
                "dense_pgvector"
                if tid in dense_ranks
                else "sparse_tsvector"
            )
        )
        fused.append(item)

    return fused


def normalize_rerank_score(raw_score: float) -> float:
    """Нормализует сырой скор / логит Cross-Encoder в диапазон [0.0, 1.0]."""
    import math

    if 0.0 <= raw_score <= 1.0:
        return round(raw_score, 4)
    try:
        prob = 1.0 / (1.0 + math.exp(-raw_score))
        return round(prob, 4)
    except OverflowError:
        return 1.0 if raw_score > 0 else 0.0


async def rerank_candidates(
    query_text: str,
    candidates: list[dict[str, Any]],
    top_n: int = 3,
    threshold: float = 0.85,
    circuit: DataCircuit | None = None,
) -> list[dict[str, Any]]:
    """
    Выполняет двухэтапную переоценку (Rerank) топ-кандидатов через локальный Cross-Encoder.
    Отбирает наиболее семантически релевантные решения (порог score >= 0.85).
    """
    if not candidates:
        return []
    if len(candidates) == 1:
        candidates[0]["rerank_score"] = candidates[0].get("similarity_pct", 85.0) / 100.0
        return candidates[:top_n]

    clean_query = clean_html(query_text).strip()
    doc_texts = []
    for c in candidates:
        name = c.get("name") or ""
        problem = c.get("problem") or ""
        solution = c.get("solution") or ""
        doc_texts.append(
            f"Тема: {name}. Проблема: {problem}. Решение: {solution}".strip()
        )

    # Выполняем rerank в отдельном потоке (worker thread)
    scores = await asyncio.to_thread(
        _rerank_fastembed_sync, clean_query, doc_texts
    )

    if scores and len(scores) == len(candidates):
        for candidate, raw_score in zip(candidates, scores):
            norm_score = normalize_rerank_score(raw_score)
            candidate["rerank_score"] = norm_score
            candidate["similarity_pct"] = round(norm_score * 100.0, 1)
            candidate["search_type"] = "hybrid_reranked"

        sorted_candidates = sorted(
            candidates,
            key=lambda x: x.get("rerank_score", 0.0),
            reverse=True,
        )
        filtered = [
            c
            for c in sorted_candidates
            if c.get("rerank_score", 0.0) >= threshold
        ]
        if not filtered:
            filtered = sorted_candidates[:top_n]
        return filtered[:top_n]

    # Fallback при отсутствии модели Cross-Encoder: сохраняем RRF / cosine порядок
    for c in candidates:
        c["rerank_fallback"] = True
    return candidates[:top_n]


async def search_knowledge_base(
    db: AsyncSession,
    query_text: str,
    limit: int = 3,
    distance_threshold: float = 0.70,
    circuit: DataCircuit | None = None,
    metadata: RoutingMetadata | None = None,
    hybrid: bool = True,
    distill_query: bool = True,
    rerank: bool = True,
    rerank_threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """
    Выполняет гибридный семантический поиск (Hybrid RRF: Dense pgvector + Sparse tsvector)
    с предварительной нормализацией запроса (Query Distillation) и двухэтапным Cross-Encoder Reranker.
    """
    clean_query = clean_html(query_text).strip()
    if not clean_query:
        return []

    eval_circuit = circuit
    if eval_circuit is None:
        dec = data_sanitizer.evaluate_circuit(
            prompt=clean_query, metadata=metadata or RoutingMetadata()
        )
        eval_circuit = dec.circuit

    # Очистка от эмоционального шума
    search_query = (
        distill_search_query(clean_query, circuit=eval_circuit)
        if distill_query
        else clean_query
    )
    if not search_query:
        search_query = clean_query

    # 1. Если гибридный режим активен
    if hybrid:
        candidate_limit = max(limit * 5, 15) if rerank else limit * 3
        dense_task = dense_vector_search(
            db=db,
            query_text=clean_query,
            limit=candidate_limit,
            distance_threshold=distance_threshold,
            circuit=eval_circuit,
            metadata=metadata,
        )
        sparse_task = sparse_text_search(
            db=db,
            query_text=search_query,
            limit=candidate_limit,
        )

        dense_matches, sparse_matches = await asyncio.gather(
            dense_task, sparse_task
        )

        if dense_matches or sparse_matches:
            fused_matches = reciprocal_rank_fusion(
                dense_results=dense_matches,
                sparse_results=sparse_matches,
                k=60,
                limit=candidate_limit,
            )
            for m in fused_matches:
                m["distilled_query"] = search_query

            # Второй этап: Cross-Encoder Rerank
            if rerank:
                reranked = await rerank_candidates(
                    query_text=search_query,
                    candidates=fused_matches,
                    top_n=limit,
                    threshold=rerank_threshold,
                    circuit=eval_circuit,
                )
                return reranked

            return fused_matches[:limit]

    # 2. Dense-only поиск (fallback)
    dense_matches = await dense_vector_search(
        db=db,
        query_text=clean_query,
        limit=limit,
        distance_threshold=distance_threshold,
        circuit=eval_circuit,
        metadata=metadata,
    )
    for m in dense_matches:
        m["distilled_query"] = search_query
    return dense_matches


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

        # Получаем историю переписки для нахождения финального решения и канонизации
        lifetime = await intraservice.get_task_lifetime(auth_b64, tid) or []
        from app.services.ai_synthesis import canonize_task_solution

        canon = canonize_task_solution(t, lifetime)
        solution_text = canon.get("solution") or ""

        if not solution_text or solution_text == "Заявка выполнена в штатном режиме.":
            if not lifetime:
                skipped_count += 1
                continue

        t_name = t.get("Name") or f"Заявка #{tid}"
        s_id = t.get("ServiceId") or 0
        s_name = t.get("ServiceName") or "Общие"
        st_name = t.get("StatusName") or "Закрыта"

        ok = await index_task_knowledge(
            db=db,
            task_id=tid,
            original_name=t_name,
            problem=canon.get("problem") or t_name,
            solution=solution_text,
            service_id=s_id,
            service_name=s_name,
            status_name=st_name,
            classification_data={
                "synced_from_history": True,
                "days": days,
                "root_cause": canon.get("root_cause", ""),
            },
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

