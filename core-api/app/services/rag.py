import asyncio
import hashlib
import logging
import re
from collections import OrderedDict
from typing import Any
import aiohttp
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.db import AsyncSessionLocal, TaskKnowledgeBase
from app.services import intraservice
from app.services.ai import DataCircuit, RoutingMetadata, data_sanitizer
from shared.json_utils import json_dumps, json_loads

logger = logging.getLogger("core_api.rag")

_EMBED_MEMORY_CACHE: OrderedDict[str, list[float]] = OrderedDict()
_EMBED_CACHE_MAX_SIZE = 4096
_EMBED_REDIS_TTL = 7 * 86400  # 7 дней
_last_embedding_error: str | None = None


async def check_embedding_health() -> tuple[bool, str]:
    """
    Выполняет пробный Pre-flight запрос к сервису генерации эмбеддингов.
    Возвращает (True, "OK: 3072 dim") или (False, "Описание ошибки").
    """
    global _last_embedding_error
    _last_embedding_error = None
    try:
        vec = await get_embedding_vector("preflight diagnostic health probe", circuit=DataCircuit.YELLOW)
        if vec and len(vec) == settings.EMBEDDING_DIMENSION:
            return True, f"OK ({settings.EMBEDDING_MODEL}, {len(vec)} dim)"
        err = _last_embedding_error or f"Не удалось получить вектор целевой размерности ({settings.EMBEDDING_DIMENSION})"
        return False, err
    except Exception as e:
        return False, str(e)


def _get_redis_safe():
    try:
        from app.services.worker import get_redis_client

        return get_redis_client()
    except Exception:
        return None


def _get_embedding_cache_key(text_val: str, circuit: DataCircuit | None) -> str:
    model_name = getattr(settings, "EMBEDDING_MODEL", "gemini-embedding-2")
    circuit_val = circuit.value if circuit else "default"
    text_hash = hashlib.sha256(text_val.encode("utf-8")).hexdigest()
    return f"rag:emb:{model_name}:{circuit_val}:{text_hash}"


async def _save_embedding_to_cache(cache_key: str, vec: list[float]) -> None:
    _EMBED_MEMORY_CACHE[cache_key] = vec
    if len(_EMBED_MEMORY_CACHE) > _EMBED_CACHE_MAX_SIZE:
        _EMBED_MEMORY_CACHE.popitem(last=False)

    try:
        redis = _get_redis_safe()
        if redis is not None:
            await redis.set(cache_key, json_dumps(vec), ex=_EMBED_REDIS_TTL)
    except Exception as e:
        logger.debug("Ошибка записи эмбеддинга в Redis кэш: %s", e)

_fastembed_model = None
_fastembed_reranker = None


def _get_onnx_hardware_providers() -> list[str]:
    """Возвращает приоритетный список аппаратных провайдеров (CUDA, DirectML, CPU)."""
    return ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]


def get_local_embed_model():
    """Ленивая загрузка локальной модели fastembed с адаптивным GPU-ускорением."""
    global _fastembed_model
    if _fastembed_model is None:
        model_name = getattr(
            settings,
            "FASTEMBED_MODEL",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
        try:
            from fastembed import TextEmbedding

            try:
                providers = _get_onnx_hardware_providers()
                _fastembed_model = TextEmbedding(model_name=model_name, providers=providers)
            except Exception:
                _fastembed_model = TextEmbedding(model_name=model_name)
        except Exception as e:
            logger.debug("Ошибка инициализации fastembed: %s", e)
    return _fastembed_model


def get_local_reranker_model():
    """Ленивая загрузка локальной модели cross-encoder fastembed с адаптивным GPU-ускорением."""
    global _fastembed_reranker
    if _fastembed_reranker is None:
        model_name = getattr(
            settings, "RERANKER_MODEL", "BAAI/bge-reranker-base"
        )
        try:
            from fastembed import TextCrossEncoder

            try:
                providers = _get_onnx_hardware_providers()
                _fastembed_reranker = TextCrossEncoder(model_name=model_name, providers=providers)
            except Exception:
                _fastembed_reranker = TextCrossEncoder(model_name=model_name)
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
    - Multi-tier Cache: быстрый LRU в RAM (0ms) + персистентный Redis (1ms, TTL 7 дней).
    """
    clean_text = clean_html(text_input).strip()[:4000]
    if not clean_text:
        return None

    cache_circuit = DataCircuit.RED if force_local else circuit
    cache_key = _get_embedding_cache_key(clean_text, cache_circuit)

    # 1. Быстрый RAM LRU Cache (0ms)
    if cache_key in _EMBED_MEMORY_CACHE:
        _EMBED_MEMORY_CACHE.move_to_end(cache_key)
        return _EMBED_MEMORY_CACHE[cache_key]

    # 2. Персистентный Redis Cache (1ms)
    try:
        redis = _get_redis_safe()
        if redis is not None:
            cached_raw = await redis.get(cache_key)
            if cached_raw:
                vec = json_loads(cached_raw)
                if isinstance(vec, list) and len(vec) == settings.EMBEDDING_DIMENSION:
                    _EMBED_MEMORY_CACHE[cache_key] = vec
                    if len(_EMBED_MEMORY_CACHE) > _EMBED_CACHE_MAX_SIZE:
                        _EMBED_MEMORY_CACHE.popitem(last=False)
                    return vec
    except Exception as e:
        logger.debug("Ошибка чтения эмбеддинга из Redis кэша: %s", e)

    # 3. Если принудительно локальный режим или RED контур -> строго локальные эмбеддеры
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
                                await _save_embedding_to_cache(cache_key, vec)
                                return vec
            except Exception:
                pass

        # Fallback: локальный FastEmbed в отдельном пуле потоков
        try:
            vec = await asyncio.to_thread(_get_fastembed_vector_sync, clean_text)
            if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                await _save_embedding_to_cache(cache_key, vec)
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

    # 4. Подготовка текста для облачных эмбеддеров (маскирование PII при YELLOW или если обнаружены сущности)
    cloud_payload_text = clean_text
    if circuit == DataCircuit.YELLOW or circuit is None:
        san_res = data_sanitizer.sanitize(clean_text)
        if san_res.detected_types:
            cloud_payload_text = san_res.sanitized_text

    session = await get_rag_http_session()

    global _last_embedding_error

    # 5. Попытка через LiteLLM Proxy
    if settings.LITELLM_BASE_URL:
        try:
            url = f"{settings.LITELLM_BASE_URL.rstrip('/')}/embeddings"
            headers = {"Authorization": f"Bearer {settings.LITELLM_API_KEY}"}
            payload = {
                "input": [cloud_payload_text],
                "model": settings.EMBEDDING_MODEL,
            }
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    vec = data.get("data", [{}])[0].get("embedding")
                    if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                        await _save_embedding_to_cache(cache_key, vec)
                        return vec
                    _last_embedding_error = f"LiteLLM вернул вектор {len(vec) if vec else 0} dim (ожидалось {settings.EMBEDDING_DIMENSION})"
                else:
                    err_txt = await resp.text()
                    _last_embedding_error = f"LiteLLM HTTP {resp.status}: {err_txt[:140]}"
                    logger.warning("Сбой генерации вектора через LiteLLM: %s", _last_embedding_error)
        except Exception as e:
            _last_embedding_error = f"LiteLLM исключение: {e}"
            logger.debug("Исключение LiteLLM Proxy: %s", e)

    # 6. Попытка напрямую через Gemini API (с ротацией ключей GEMINI_API_KEY, _2, _3)
    gemini_keys = [
        getattr(settings, "GEMINI_API_KEY", None),
        getattr(settings, "GEMINI_API_KEY_2", None),
        getattr(settings, "GEMINI_API_KEY_3", None),
    ]
    gemini_keys = [k for k in gemini_keys if k]
    if gemini_keys:
        candidate_models = [getattr(settings, "EMBEDDING_MODEL", "gemini-embedding-001"), "gemini-embedding-001"]
        for g_key in gemini_keys:
            for embed_model in dict.fromkeys(candidate_models):
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{embed_model}:embedContent?key={g_key}"
                    payload = {
                        "model": f"models/{embed_model}",
                        "content": {"parts": [{"text": cloud_payload_text}]},
                    }
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            vec = data.get("embedding", {}).get("values", [])
                            if vec and len(vec) == settings.EMBEDDING_DIMENSION:
                                await _save_embedding_to_cache(cache_key, vec)
                                return vec
                            _last_embedding_error = f"Gemini API вернул {len(vec)} dim (ожидалось {settings.EMBEDDING_DIMENSION})"
                        else:
                            err_txt = await resp.text()
                            if resp.status == 401:
                                continue
                            _last_embedding_error = f"Gemini API HTTP {resp.status} ({embed_model}): {err_txt[:140]}"
                            logger.debug("Сбой прямого Gemini API: %s", _last_embedding_error)
                except Exception as e:
                    _last_embedding_error = f"Gemini API исключение ({embed_model}): {e}"
                    logger.debug("Ошибка генерации Gemini эмбеддинга для %s: %s", embed_model, e)

    # 7. Локальный Fallback (Ollama / FastEmbed)
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
                            await _save_embedding_to_cache(cache_key, vec)
                            return vec
        except Exception:
            pass

    try:
        vec = await asyncio.to_thread(_get_fastembed_vector_sync, clean_text)
        if vec:
            if len(vec) == settings.EMBEDDING_DIMENSION:
                await _save_embedding_to_cache(cache_key, vec)
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

    # Проверяем, есть ли вообще записи в базе знаний перед вызовом внешнего сервиса эмбеддингов
    try:
        has_records = await db.scalar(select(TaskKnowledgeBase.task_id).limit(1))
        if not has_records:
            return []
    except Exception:
        pass

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

    # Кэш результатов RAG в Redis (TTL 10 минут) для устранения повторных эмбеддингов
    import hashlib
    import json
    from app.services.worker import get_redis_client

    cache_key = (
        f"rag:cache:{hashlib.md5(search_query.encode()).hexdigest()}:"
        f"{eval_circuit.value if eval_circuit else 'auto'}:{limit}:"
        f"{distance_threshold}:{int(hybrid)}:{int(rerank)}:{rerank_threshold}"
    )
    try:
        redis = get_redis_client()
        cached = await redis.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    final_matches: list[dict[str, Any]] = []

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
                final_matches = reranked
            else:
                final_matches = fused_matches[:limit]

    # 2. Dense-only поиск (fallback)
    if not final_matches:
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
        final_matches = dense_matches

    # Сохранение в Redis кэш
    if final_matches:
        try:
            redis = get_redis_client()
            await redis.set(cache_key, json.dumps(final_matches), ex=600)
        except Exception:
            pass

    return final_matches


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
        from app.services.ai_synthesis import is_informative_solution
        if not is_informative_solution(solution):
            logger.debug("Заявка #%d отклонена Quality Gate RAG (неинформативное решение: '%s')", task_id, (solution or "")[:50])
            return False

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
        "StatusIds": "29,30",
        "ChangedMoreThan": cutoff_str,
        "pagesize": str(min(max(limit, 20), 100)),
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

        # Quality Gate: отсекаем неинформативные заявки без полезного технического решения
        from app.services.ai_synthesis import is_informative_solution
        if not is_informative_solution(solution_text):
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


# ---------------------------------------------------------------------------
# 6. Умное стратифицированное квотирование и наполнение RAG (01–17)
# ---------------------------------------------------------------------------


async def check_semantic_duplicate(
    db: AsyncSession,
    vector: list[float],
    service_ids: list[int] | None = None,
    threshold: float = 0.90,
) -> bool:
    """
    Проверяет наличие семантического дубликата задачи в базе (Cosine similarity >= threshold).
    Порог 0.90 гарантирует разнообразие прецедентов внутри раздела.
    """
    if not vector:
        return False
    try:
        max_dist = 1.0 - threshold
        dist_expr = TaskKnowledgeBase.embedding.cosine_distance(vector).label("dist")
        conditions = [
            TaskKnowledgeBase.embedding.is_not(None),
            TaskKnowledgeBase.is_blacklisted.is_(False),
        ]
        if service_ids:
            conditions.append(TaskKnowledgeBase.service_id.in_(service_ids))

        stmt = (
            select(dist_expr)
            .where(*conditions)
            .order_by("dist")
            .limit(1)
        )
        res = await db.execute(stmt)
        min_dist = res.scalar_one_or_none()
        if min_dist is not None and float(min_dist) <= max_dist:
            return True
        return False
    except Exception as e:
        logger.debug("Ошибка проверки семантического дубликата: %s", e)
        return False


def get_subservice_ids_for_root(root_key: str) -> list[int]:
    """Возвращает список всех ID услуг IntraService, относящихся к корневому разделу root_key."""
    from app.services.rules.catalog import ROOT_SERVICES, SERVICE_ID_TO_ROOT
    res = set()
    if root_key in ROOT_SERVICES:
        res.add(ROOT_SERVICES[root_key]["id"])
    for sid, r in SERVICE_ID_TO_ROOT.items():
        if r == root_key:
            res.add(sid)
    return sorted(list(res))


def get_all_root_services() -> list[dict[str, Any]]:
    """Возвращает список корневых разделов каталога (01..16)."""
    from app.services.rules.catalog import ROOT_SERVICES
    items = []
    for key, info in sorted(ROOT_SERVICES.items()):
        items.append({
            "root_id": key,
            "root_service_id": info["id"],
            "name": info["name"],
        })
    return items


async def get_kb_sync_progress() -> dict[str, Any]:
    """Возвращает текущий прогресс фоновой синхронизации базы знаний из Redis."""
    redis = _get_redis_safe()
    if redis is None:
        return {"is_running": False, "percent": 0, "message": "Redis недоступен"}
    try:
        raw = await redis.get("kb:sync_progress")
        if raw:
            state = json_loads(raw)
            # Edge Case: Защита от зависшего статуса при аварийном прерывании или перезапуске контейнера
            if state.get("is_running") and state.get("updated_at"):
                try:
                    upd = datetime.fromisoformat(state["updated_at"])
                    if datetime.now(timezone.utc) - upd > timedelta(seconds=90):
                        state["is_running"] = False
                        state["error"] = "Процесс синхронизации был прерван (перезапуск контейнера или сбой воркера)"
                        state["finished_at"] = datetime.now(timezone.utc).isoformat()
                        await _save_sync_progress(redis, state)
                        await redis.delete("lock:kb_sync")
                except Exception:
                    pass
            return state
    except Exception as e:
        logger.debug("Ошибка чтения kb:sync_progress: %s", e)
    return {
        "is_running": False,
        "percent": 0,
        "total_indexed": 0,
        "total_skipped": 0,
        "total_duplicates": 0,
        "current_service_name": None,
        "service_stats": {},
    }


async def _save_sync_progress(redis, state: dict[str, Any]) -> None:
    if redis:
        try:
            await redis.set("kb:sync_progress", json_dumps(state), ex=3600)
        except Exception as e:
            logger.debug("Ошибка сохранения kb:sync_progress в Redis: %s", e)


async def sync_stratified_kb(
    auth_b64: str,
    quota_per_service: int = 30,
    days: int = 60,
    target_root_id: str | None = None,
) -> dict[str, Any]:
    """
    Умное фоновое наполнение RAG по корневым разделам IntraService (01..17).
    - Защита от перегрузки (Rate Limiting + троттлинг).
    - Квотирование на каждый сервис (добирает только недостающие до quota_per_service).
    - Семантическая дедупликация (Cosine Gate > 0.90) для разнообразия прецедентов.
    - Уважение черного списка (Blacklist Integrity).
    - Обрезка длинных логов до 2000 символов.
    - Ограничение по времени и Redis Leader Lock от дублирования запусков.
    """
    from datetime import datetime, timedelta, timezone

    redis = _get_redis_safe()
    lock_key = "lock:kb_sync"

    # Edge Case 4: Re-entrancy / Защита от параллельного запуска
    if redis:
        acquired = await redis.set(lock_key, "1", nx=True, ex=900)
        if not acquired:
            raise RuntimeError(
                "Синхронизация базы знаний уже выполняется другим процессом."
            )

    all_roots = get_all_root_services()
    if target_root_id:
        target_roots = [r for r in all_roots if r["root_id"] == target_root_id]
        if not target_roots:
            if redis:
                await redis.delete(lock_key)
            raise ValueError(f"Корневой раздел с ID '{target_root_id}' не найден в каталоге.")
    else:
        target_roots = all_roots

    progress_state: dict[str, Any] = {
        "is_running": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "target_root_id": target_root_id,
        "current_root": None,
        "current_service_name": None,
        "processed_roots": 0,
        "total_roots": len(target_roots),
        "percent": 0,
        "total_indexed": 0,
        "total_skipped": 0,
        "total_duplicates": 0,
        "total_ai_errors": 0,
        "service_stats": {},
        "logs": [],
        "error": None,
        "finished_at": None,
    }

    def add_log(msg: str, level: str = "info") -> None:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        progress_state.setdefault("logs", []).append({
            "time": now_str,
            "level": level,
            "message": msg,
        })
        if len(progress_state["logs"]) > 100:
            progress_state["logs"] = progress_state["logs"][-100:]

    add_log(f"Старт наполнения RAG: {len(target_roots)} разделов, квота {quota_per_service}, глубина {days} дн.", "info")
    await _save_sync_progress(redis, progress_state)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M")

    try:
        consecutive_ai_errors = 0
        async with AsyncSessionLocal() as db:
            for idx, root_info in enumerate(target_roots, start=1):
                r_id = root_info["root_id"]
                r_name = root_info["name"]
                sub_ids = get_subservice_ids_for_root(r_id)

                progress_state["current_root"] = r_id
                progress_state["current_service_name"] = r_name
                progress_state["updated_at"] = datetime.now(timezone.utc).isoformat()
                progress_state["percent"] = int(((idx - 1) / len(target_roots)) * 100)
                await _save_sync_progress(redis, progress_state)

                if not sub_ids:
                    progress_state["processed_roots"] = idx
                    add_log(f"[{r_id}] Раздел '{r_name}': нет дочерних сервисов, пропуск", "warn")
                    continue

                # Проверяем, сколько активных прецедентов уже есть в этом разделе
                count_stmt = select(func.count(TaskKnowledgeBase.task_id)).where(
                    TaskKnowledgeBase.service_id.in_(sub_ids),
                    TaskKnowledgeBase.is_blacklisted.is_(False),
                )
                existing_count = (await db.execute(count_stmt)).scalar() or 0

                s_stats = {
                    "name": r_name,
                    "existing": existing_count,
                    "indexed": 0,
                    "skipped": 0,
                    "duplicates": 0,
                    "quota": quota_per_service,
                    "status": "in_progress",
                }

                add_log(f"[{r_id}] Раздел '{r_name}': в базе {existing_count}/{quota_per_service} записей", "info")

                if existing_count >= quota_per_service:
                    s_stats["status"] = "quota_reached"
                    progress_state["service_stats"][r_id] = s_stats
                    progress_state["processed_roots"] = idx
                    add_log(f"[{r_id}] Раздел '{r_name}' укомплектован (квота {quota_per_service} достигнута)", "success")
                    await _save_sync_progress(redis, progress_state)
                    continue

                needed = quota_per_service - existing_count
                page = 1
                page_size = min(max(needed * 2, 20), 50)
                service_indexed = 0

                while service_indexed < needed and page <= 10:
                    params = {
                        "StatusIds": "29,30",
                        "serviceids": ",".join(str(s) for s in sub_ids),
                        "ChangedMoreThan": cutoff_str,
                        "pagesize": str(page_size),
                        "page": str(page),
                        "include": "status,service",
                    }
                    try:
                        raw_tasks = await intraservice.get_tasks(auth_b64=auth_b64, filters=params)
                    except Exception as fe:
                        err_str = str(fe)
                        logger.warning("Сбой выборки задач для раздела %s (стр %d): %s", r_name, page, fe)
                        if "401" in err_str or "Unauthorized" in err_str:
                            err_msg = f"Ошибка авторизации IntraService (401 Unauthorized): проверьте пароль в Хранилище"
                            add_log(err_msg, "error")
                            progress_state["is_running"] = False
                            progress_state["error"] = err_msg
                            progress_state["finished_at"] = datetime.now(timezone.utc).isoformat()
                            await _save_sync_progress(redis, progress_state)
                            return progress_state
                        add_log(f"[{r_id}] Ошибка загрузки страницы {page}: {fe}", "warn")
                        break

                    batch = []
                    if isinstance(raw_tasks, dict):
                        batch = raw_tasks.get("Tasks", [])
                    elif isinstance(raw_tasks, list):
                        batch = raw_tasks

                    if not batch:
                        add_log(f"[{r_id}] Закрытых заявок больше нет", "info")
                        break

                    add_log(f"[{r_id}] Получено {len(batch)} заявок на стр. {page}", "info")

                    for t in batch:
                        if service_indexed >= needed:
                            break
                        tid = t.get("Id")
                        if not tid:
                            continue

                        # Edge Case 5: Blacklist Integrity & проверка наличия в БД
                        check_db = await db.execute(
                            select(TaskKnowledgeBase.task_id).where(TaskKnowledgeBase.task_id == tid)
                        )
                        if check_db.scalar_one_or_none():
                            s_stats["skipped"] += 1
                            progress_state["total_skipped"] += 1
                            continue

                        # Edge Case 1: Throttling / соблюдение Rate Limit (15-20 RPM)
                        await asyncio.sleep(1.5)

                        try:
                            lifetime = await intraservice.get_task_lifetime(auth_b64, tid) or []
                        except Exception as lte:
                            logger.debug("Ошибка получения lifetime для заявки #%d: %s", tid, lte)
                            lifetime = []

                        from app.services.ai_synthesis import canonize_task_solution, is_informative_solution

                        canon = canonize_task_solution(t, lifetime)
                        solution_text = canon.get("solution") or ""

                        # Quality Gate: отсекаем шаблонные отписки
                        if not is_informative_solution(solution_text):
                            s_stats["skipped"] += 1
                            progress_state["total_skipped"] += 1
                            add_log(f"#{tid}: отсеяна Quality Gate (неинформативно)", "warn")
                            continue

                        # Edge Case 7: обрезка чрезмерно длинных логов
                        solution_text = solution_text[:2000]
                        problem_text = (canon.get("problem") or t.get("Name") or f"Заявка #{tid}")[:1000]
                        t_name = (t.get("Name") or f"Заявка #{tid}")[:255]
                        s_id = t.get("ServiceId") or sub_ids[0]
                        s_name = t.get("ServiceName") or r_name
                        st_name = t.get("StatusName") or "Закрыта"

                        embed_input = f"Тема: {t_name}\nПроблема: {problem_text}\nРешение: {solution_text}"

                        vec = await get_embedding_vector(embed_input)
                        if not vec:
                            err_reason = _last_embedding_error or "сервис генерации векторов вернул None"
                            # Если это Rate Limit (429), делаем вежливую паузу и 1 повторную попытку
                            if "429" in err_reason or "quota" in err_reason.lower() or "limit" in err_reason.lower():
                                add_log(f"#{tid}: лимит запросов AI (429), пауза 4.5с...", "warn")
                                await asyncio.sleep(4.5)
                                vec = await get_embedding_vector(embed_input)

                        if not vec:
                            consecutive_ai_errors += 1
                            progress_state["total_ai_errors"] = progress_state.get("total_ai_errors", 0) + 1
                            s_stats["ai_errors"] = s_stats.get("ai_errors", 0) + 1
                            err_reason = _last_embedding_error or "сервис генерации векторов вернул None"
                            add_log(f"#{tid}: сбой AI эмбеддера ({err_reason[:60]})", "error")
                            logger.warning(
                                "Сбой генерации вектора для заявки #%d (сбоев подряд: %d): %s",
                                tid,
                                consecutive_ai_errors,
                                err_reason,
                            )
                            if consecutive_ai_errors >= 3:
                                err_msg = (
                                    f"Circuit Breaker: 3 сбоя генерации векторов подряд. "
                                    f"Причина: {err_reason}. "
                                    f"Синхронизация аварийно остановлена во избежание холостого прогона."
                                )
                                logger.error(err_msg)
                                add_log(err_msg, "error")
                                progress_state["is_running"] = False
                                progress_state["error"] = err_msg
                                progress_state["finished_at"] = datetime.now(timezone.utc).isoformat()
                                await _save_sync_progress(redis, progress_state)
                                return progress_state
                            continue

                        # Успех: сбрасываем счетчик подряд идущих сбоев AI
                        consecutive_ai_errors = 0

                        # Edge Case 3: Семантическая дедупликация (Cosine Gate > 0.90)
                        is_dup = await check_semantic_duplicate(
                            db, vec, service_ids=sub_ids, threshold=0.90
                        )
                        if is_dup:
                            s_stats["duplicates"] += 1
                            progress_state["total_duplicates"] += 1
                            add_log(f"#{tid}: отсеяна Cosine Gate (дубликат > 0.90)", "warn")
                            continue

                        # Сохранение качественного прецедента в pgvector
                        item = TaskKnowledgeBase(
                            task_id=tid,
                            original_name=t_name,
                            problem=problem_text,
                            solution=solution_text,
                            service_id=s_id,
                            service_name=s_name,
                            status_name=st_name,
                            classification_data={
                                "synced_from_history": True,
                                "root_id": r_id,
                                "days": days,
                                "root_cause": canon.get("root_cause", ""),
                            },
                            embedding=vec,
                            is_blacklisted=False,
                        )
                        db.add(item)
                        await db.commit()

                        service_indexed += 1
                        s_stats["indexed"] += 1
                        progress_state["total_indexed"] += 1
                        add_log(f"#{tid}: сохранена в RAG ({t_name[:35]}...) [{service_indexed}/{needed}]", "success")

                    if len(batch) < page_size:
                        # Завершение страниц раздела
                        break
                    page += 1

                s_stats["status"] = "completed"
                progress_state["service_stats"][r_id] = s_stats
                progress_state["processed_roots"] = idx
                progress_state["percent"] = int((idx / len(target_roots)) * 100)
                add_log(f"[{r_id}] Раздел '{r_name}' завершен: +{s_stats['indexed']} добавлено, {s_stats['skipped']} отписок", "info")
                await _save_sync_progress(redis, progress_state)

        progress_state["is_running"] = False
        progress_state["percent"] = 100
        progress_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        add_log(f"Синхронизация RAG завершена! Всего добавлено: +{progress_state['total_indexed']} прецедентов", "success")
        await _save_sync_progress(redis, progress_state)
        logger.info(
            "Стратифицированная синхронизация RAG завершена: добавлено %d прецедентов, пропущено %d (дубликатов %d)",
            progress_state["total_indexed"],
            progress_state["total_skipped"],
            progress_state["total_duplicates"],
        )
        return progress_state

    except Exception as e:
        logger.exception("Сбой при стратифицированной синхронизации RAG: %s", e)
        progress_state["is_running"] = False
        progress_state["error"] = str(e)
        progress_state["finished_at"] = datetime.now(timezone.utc).isoformat()
        await _save_sync_progress(redis, progress_state)
        raise
    finally:
        if redis:
            try:
                await redis.delete(lock_key)
            except Exception:
                pass

