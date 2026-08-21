import asyncio
import json
import logging
import os
import re
import warnings
from typing import Any
import aiohttp
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)
logger = logging.getLogger("helpdesk_agent.kb")

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/intraservice"
)
LOCAL_DB_FILE = os.path.join(os.path.dirname(__file__), "knowledge_base.json")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-intraservice-master-key")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-2")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "3072"))

_fastembed_model = None


def get_local_embed_model():
    """Ленивая загрузка локальной модели fastembed (384 dim)."""
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


# ---------------------------------------------------------------------------
# 1. Генерация эмбеддингов: Tier 1 (Gemini/LiteLLM 3072) -> Tier 2 (FastEmbed 384)
# ---------------------------------------------------------------------------

async def get_gemini_3072_embedding(text_input: str) -> list[float] | None:
    """
    Генерирует вектор размерности 3072 через LiteLLM Proxy или напрямую через Gemini API.
    """
    clean_text = clean_html(text_input).strip()[:4000]
    if not clean_text:
        return None

    # 1. Попытка через LiteLLM Proxy (OpenAI endpoint)
    if LITELLM_BASE_URL:
        try:
            url = f"{LITELLM_BASE_URL.rstrip('/')}/embeddings"
            headers = {"Authorization": f"Bearer {LITELLM_API_KEY}"}
            payload = {"input": [clean_text], "model": EMBEDDING_MODEL}
            timeout = aiohttp.ClientTimeout(total=3.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vec = data.get("data", [{}])[0].get("embedding")
                        if vec and len(vec) == EMBEDDING_DIMENSION:
                            return vec
        except Exception:
            pass

    # 2. Попытка через Google Generative Language REST API (при прямом ключе)
    if GEMINI_KEY:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={GEMINI_KEY}"
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
                        # Принимаем только если размерность строго совпадает с целевой схемой
                        if vec and len(vec) == EMBEDDING_DIMENSION:
                            return vec
        except Exception:
            pass

    return None


def get_fastembed_vector(text_input: str) -> list[float]:
    """Быстрая локальная векторизация на CPU (384 dim)."""
    clean_text = clean_html(text_input).strip()
    if not clean_text:
        return [0.0] * 384
    model = get_local_embed_model()
    if model is not None:
        try:
            vectors = list(model.embed([clean_text]))
            if vectors:
                return vectors[0].tolist()
        except Exception as e:
            logger.debug("Ошибка fastembed: %s", e)
    return [0.0] * 384


async def get_hybrid_embedding(text_input: str) -> list[float]:
    """
    Гибридный генератор: пытается получить вектор 3072 dim (Gemini/LiteLLM),
    а при сбое переключается на локальный 384 dim (FastEmbed).
    """
    gemini_vec = await get_gemini_3072_embedding(text_input)
    if gemini_vec:
        return gemini_vec
    return get_fastembed_vector(text_input)


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Вычисляет косинусное сходство между двумя векторами (от 0.0 до 1.0)."""
    a = np.array(v1, dtype=np.float32)
    b = np.array(v2, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# 2. Подключение к хранилищам (PostgreSQL pgvector + JSON Cache)
# ---------------------------------------------------------------------------

def get_clean_dsn() -> str:
    """Приводит DATABASE_URL к формату asyncpg (без +asyncpg)."""
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def get_pg_connection():
    """
    Возвращает активное соединение с PostgreSQL и регистрирует pgvector.
    Возвращает None, если БД недоступна.
    """
    try:
        import asyncpg
        from pgvector.asyncpg import register_vector

        dsn = get_clean_dsn()
        conn = await asyncpg.connect(dsn=dsn, timeout=2.0)
        await register_vector(conn)
        return conn
    except Exception as e:
        logger.debug("PostgreSQL недоступен: %s", e)
        return None


def load_local_kb() -> dict[str, dict[str, Any]]:
    """Загружает локальную базу знаний из JSON-файла."""
    if os.path.exists(LOCAL_DB_FILE):
        try:
            try:
                import orjson
                with open(LOCAL_DB_FILE, "rb") as f:
                    return orjson.loads(f.read())
            except ImportError:
                with open(LOCAL_DB_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning("Ошибка чтения локального KB %s: %s", LOCAL_DB_FILE, e)
    return {}


def save_local_kb(kb_data: dict[str, dict[str, Any]]):
    """Атомарно сохраняет локальную базу знаний."""
    try:
        temp_file = LOCAL_DB_FILE + ".tmp"
        try:
            import orjson
            with open(temp_file, "wb") as f:
                f.write(orjson.dumps(kb_data, option=orjson.OPT_INDENT_2))
        except ImportError:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(kb_data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, LOCAL_DB_FILE)
    except Exception as e:
        logger.error("Ошибка сохранения KB: %s", e)


async def test_db_connection() -> tuple[bool, str]:
    """Проверяет статус подключения к Tier 1 (PostgreSQL) и Tier 2 (Локальный кэш)."""
    conn = await get_pg_connection()
    if conn:
        try:
            val = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = 'task_knowledge_base';"
            )
            await conn.close()
            if val and val > 0:
                return True, "🟢 Tier 1: PostgreSQL + pgvector подключен (таблица task_knowledge_base активна)"
            return True, "🟡 Tier 1: PostgreSQL доступен, но таблица task_knowledge_base еще не создана"
        except Exception as e:
            return False, f"🔴 Ошибка запроса к PostgreSQL: {e}"

    local_kb = load_local_kb()
    return True, f"🔵 Tier 2 (Fallback): Активен локальный кэш knowledge_base.json ({len(local_kb)} записей)"


async def ensure_db_schema() -> bool:
    """Создает расширение vector и таблицу task_knowledge_base в PostgreSQL, если они отсутствуют."""
    conn = await get_pg_connection()
    if not conn:
        return False
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS task_knowledge_base (
                task_id INTEGER PRIMARY KEY,
                original_name VARCHAR NOT NULL,
                problem VARCHAR NOT NULL,
                solution VARCHAR NOT NULL,
                service_id INTEGER NOT NULL,
                service_name VARCHAR NOT NULL,
                status_name VARCHAR NOT NULL,
                classification_data JSONB NOT NULL,
                embedding vector({EMBEDDING_DIMENSION}),
                is_blacklisted BOOLEAN NOT NULL DEFAULT false
            );
        """)
        await conn.close()
        return True
    except Exception as e:
        logger.error("Ошибка инициализации схемы PostgreSQL: %s", e)
        try:
            await conn.close()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# 3. Умный фильтр качества (Smart Quality Filter)
# ---------------------------------------------------------------------------

TRIVIAL_WORDS = {
    "ок", "ок.", "ок!", "сделано", "сделал", "готово", "готово.", "выполнено", "выполнил",
    "закрыто", "закрыта", "решено", "++", "+", "исправлено", "настроено", "сделано.",
    "созвонились", "решили", "передал", "передано", "тест", "проверено", "заявка не актуальна",
    "не актуально", "отменено", "отмена", "не дозвонились", "не дозвонился", "в работе"
}


def is_trivial_solution(text_val: str) -> bool:
    """Определяет, является ли решение пустым, шаблонно-мусорным или тривиальным (Quality Gate)."""
    cleaned = clean_html(text_val).strip().lower().rstrip(".! ")
    if not cleaned or len(cleaned) < 15:
        return True
    if cleaned in TRIVIAL_WORDS:
        return True
    # Проверка на вырожденные фразы из 1-2 служебных слов
    words = [w for w in re.split(r"\s+", cleaned) if len(w) > 1]
    if len(words) <= 2 and any(w in TRIVIAL_WORDS for w in words):
        return True
    return False


def smart_filter_task(task: dict[str, Any], comments: list[dict[str, Any]]) -> tuple[bool, str, dict[str, Any]]:
    """
    Анализирует задачу и комментарии на пригодность для сохранения в базу знаний.
    """
    task_id = task.get("Id")
    name = clean_html(task.get("Name", "")).strip()
    desc = clean_html(task.get("Description", "")).strip()
    service_name = task.get("ServiceName") or f"ID {task.get('ServiceId')}"
    service_id = task.get("ServiceId") or 0
    status_id = task.get("StatusId")
    status_name = task.get("StatusName") or ""
    creator = task.get("Creator") or ""

    if not name and not desc:
        return False, "Отсутствует тема и описание инцидента", {}

    problem_text = f"{name}. {desc}".strip()

    engineer_comments = []
    for c in comments:
        c_text = clean_html(c.get("Comments") or c.get("Comment") or c.get("Text") or c.get("Description") or "").strip()
        c_author = c.get("Editor") or c.get("UserName") or c.get("Creator") or ""
        if creator and c_author.lower() == creator.lower():
            continue
        if c_text and not is_trivial_solution(c_text):
            engineer_comments.append(c_text)

    # 1. Выполненные заявки (Status 29)
    if status_id == 29 or "выполнен" in status_name.lower():
        if not engineer_comments:
            return False, "Нет содержательного комментария с описанием решения", {}

        best_solution = "\n".join(engineer_comments)
        return True, "Качественное выполненное решение", {
            "task_id": task_id,
            "original_name": name,
            "problem": problem_text,
            "solution": best_solution,
            "service_id": service_id,
            "service_name": service_name,
            "status_name": "Выполнена",
            "classification_data": {
                "type": "resolved",
                "char_length": len(best_solution),
            },
        }

    # 2. Отмененные заявки (Status 30) с перенаправлением
    if status_id == 30 or "отменен" in status_name.lower():
        redirection_comment = None
        for c_text in engineer_comments:
            if any(w in c_text.lower() for w in ["не в подходящем разделе", "требуется оставить заявку в", "перенаправ"]):
                redirection_comment = c_text
                break

        if redirection_comment:
            return True, "Перенаправление некорректно созданной заявки", {
                "task_id": task_id,
                "original_name": name,
                "problem": problem_text,
                "solution": redirection_comment,
                "service_id": service_id,
                "service_name": service_name,
                "status_name": "Отменена",
                "classification_data": {
                    "type": "redirect_cancel",
                    "source_service": service_name,
                },
            }
        return False, "Отмененная заявка без ясного перенаправления", {}

    return False, f"Статус #{status_id} не входит в целевые (29/30)", {}


# ---------------------------------------------------------------------------
# 4. Двухуровневый векторный поиск (Tier 1 PostgreSQL -> Tier 2 Local JSON)
# ---------------------------------------------------------------------------

async def search_pgvector(
    query_text: str, limit: int = 3, distance_threshold: float = 0.70
) -> list[dict[str, Any]] | None:
    """Поиск по Tier 1 (PostgreSQL + pgvector 3072 dim)."""
    query_vector = await get_gemini_3072_embedding(query_text)
    if not query_vector:
        return None

    conn = await get_pg_connection()
    if not conn:
        return None

    try:
        sql = """
            SELECT task_id, original_name, problem, solution, service_name, status_name, classification_data,
                   embedding <=> $1 AS distance
            FROM task_knowledge_base
            WHERE embedding IS NOT NULL AND is_blacklisted = false
            ORDER BY distance ASC
            LIMIT $2;
        """
        rows = await conn.fetch(sql, query_vector, limit)
        await conn.close()

        results = []
        for r in rows:
            dist = float(r["distance"])
            if dist <= distance_threshold:
                sim_pct = round(max(0.0, min(99.0, (1.0 - dist) * 100.0)), 1)
                results.append({
                    "task_id": r["task_id"],
                    "name": r["original_name"],
                    "problem": r["problem"],
                    "solution": r["solution"],
                    "service_name": r["service_name"],
                    "status_name": r["status_name"],
                    "similarity_pct": sim_pct,
                    "distance": round(dist, 4),
                    "storage_tier": "PostgreSQL (pgvector 3072)",
                })
        return results
    except Exception as e:
        logger.debug("Ошибка поиска в pgvector: %s", e)
        try:
            await conn.close()
        except Exception:
            pass
        return None


def search_local_kb(
    query_vector: list[float], limit: int = 3, distance_threshold: float = 0.70
) -> list[dict[str, Any]]:
    """Поиск по Tier 2 (Локальный JSON + FastEmbed 384 dim)."""
    kb_data = load_local_kb()
    if not kb_data:
        return []

    scored_items = []
    for str_id, item in kb_data.items():
        item_vector = item.get("embedding")
        if not item_vector or len(item_vector) != len(query_vector):
            continue

        sim = cosine_similarity(query_vector, item_vector)
        dist = 1.0 - sim

        if dist <= distance_threshold:
            sim_clamped = max(0.0, min(1.0, sim))
            similarity_pct = round(min(99.0, sim_clamped * 140.0), 1)
            scored_items.append({
                "task_id": item.get("task_id"),
                "name": item.get("original_name"),
                "problem": item.get("problem"),
                "solution": item.get("solution"),
                "service_name": item.get("service_name"),
                "status_name": item.get("status_name"),
                "similarity_pct": similarity_pct,
                "distance": round(dist, 4),
                "storage_tier": "Локальный кэш (FastEmbed 384)",
            })

    scored_items.sort(key=lambda x: x["similarity_pct"], reverse=True)
    return scored_items[:limit]


async def search_knowledge_base(
    query_text: str, limit: int = 3, distance_threshold: float = 0.70
) -> list[dict[str, Any]]:
    """
    Двухуровневый семантический поиск:
    1. Tier 1: PostgreSQL + pgvector (3072 dim Gemini embeddings)
    2. Tier 2 (Fallback): Локальный JSON кэш (FastEmbed 384 dim)
    """
    clean_query = clean_html(query_text).strip()
    if not clean_query:
        return []

    # 1. Попытка поиска в PostgreSQL (Tier 1)
    pg_results = await search_pgvector(clean_query, limit=limit, distance_threshold=distance_threshold)
    if pg_results is not None:
        return pg_results

    # 2. Fallback: локальный поиск в памяти (Tier 2)
    local_vector = get_fastembed_vector(clean_query)
    return search_local_kb(local_vector, limit=limit, distance_threshold=distance_threshold)


# ---------------------------------------------------------------------------
# 5. Двухуровневая индексация (Tier 1 PostgreSQL -> Tier 2 Local JSON)
# ---------------------------------------------------------------------------

async def index_pgvector(
    task_id: int,
    original_name: str,
    problem: str,
    solution: str,
    service_id: int,
    service_name: str,
    status_name: str,
    classification_data: dict[str, Any] | None = None,
) -> bool:
    """Индексирует решение в PostgreSQL (Tier 1)."""
    embed_input = f"Тема: {original_name}\nПроблема: {problem}\nРешение: {solution}"
    vec_3072 = await get_gemini_3072_embedding(embed_input)
    if not vec_3072:
        return False

    conn = await get_pg_connection()
    if not conn:
        return False

    try:
        sql = """
            INSERT INTO task_knowledge_base (
                task_id, original_name, problem, solution,
                service_id, service_name, status_name,
                classification_data, embedding, is_blacklisted
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, $9, false
            )
            ON CONFLICT (task_id) DO UPDATE SET
                original_name = EXCLUDED.original_name,
                problem = EXCLUDED.problem,
                solution = EXCLUDED.solution,
                service_id = EXCLUDED.service_id,
                service_name = EXCLUDED.service_name,
                status_name = EXCLUDED.status_name,
                classification_data = EXCLUDED.classification_data,
                embedding = EXCLUDED.embedding,
                is_blacklisted = false;
        """
        cls_json = json.dumps(classification_data or {})
        await conn.execute(
            sql,
            task_id,
            original_name,
            problem,
            solution,
            service_id,
            service_name,
            status_name,
            cls_json,
            vec_3072,
        )
        await conn.close()
        return True
    except Exception as e:
        logger.debug("Ошибка вставки в pgvector: %s", e)
        try:
            await conn.close()
        except Exception:
            pass
        return False


def index_local_cache(
    task_id: int,
    original_name: str,
    problem: str,
    solution: str,
    service_id: int,
    service_name: str,
    status_name: str,
    classification_data: dict[str, Any] | None = None,
) -> bool:
    """Индексирует решение в локальный JSON-кэш (Tier 2)."""
    try:
        embed_input = f"Тема: {original_name}\nПроблема: {problem}\nРешение: {solution}"
        vec_384 = get_fastembed_vector(embed_input)

        kb_data = load_local_kb()
        str_id = str(task_id)
        kb_data[str_id] = {
            "task_id": task_id,
            "original_name": original_name,
            "problem": problem,
            "solution": solution,
            "service_id": service_id,
            "service_name": service_name,
            "status_name": status_name,
            "classification_data": classification_data or {},
            "embedding": vec_384,
        }
        save_local_kb(kb_data)
        return True
    except Exception as e:
        logger.error("Ошибка сохранения в локальный кэш: %s", e)
        return False


async def index_task_record(
    task_id: int,
    original_name: str,
    problem: str,
    solution: str,
    service_id: int,
    service_name: str,
    status_name: str,
    classification_data: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """
    Индексирует решение заявки:
    - Пробует сохранить в PostgreSQL (pgvector 3072).
    - При сбое или отсутствии БД сохраняет в локальный fallback-кэш (FastEmbed 384).
    """
    ok_pg = await index_pgvector(
        task_id=task_id,
        original_name=original_name,
        problem=problem,
        solution=solution,
        service_id=service_id,
        service_name=service_name,
        status_name=status_name,
        classification_data=classification_data,
    )
    if ok_pg:
        return True, "PostgreSQL (pgvector 3072)"

    ok_local = index_local_cache(
        task_id=task_id,
        original_name=original_name,
        problem=problem,
        solution=solution,
        service_id=service_id,
        service_name=service_name,
        status_name=status_name,
        classification_data=classification_data,
    )
    if ok_local:
        return True, "Локальный кэш (FastEmbed 384)"

    return False, "Ошибка сохранения"


# ---------------------------------------------------------------------------
# 6. Пакетная синхронизация (Sync History KB)
# ---------------------------------------------------------------------------

async def sync_history_kb(
    is_client: Any,
    limit: int = 50,
    days: int = 30,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Выгружает выполненные (29) и отмененные (30) заявки, применяет умный фильтр качества и индексирует.
    """
    stats = {
        "fetched": 0,
        "accepted": 0,
        "skipped": 0,
        "indexed": 0,
        "storage_breakdown": {"pgvector": 0, "local_cache": 0},
        "reasons": {},
    }
    print("Запрос выполненных (29) и отмененных (30) заявок из IntraService...")

    half_limit = max(10, limit // 2)
    tasks_29 = await is_client.get_tasks_by_status(status_ids=[29], page=1, page_size=half_limit)
    tasks_30 = await is_client.get_tasks_by_status(status_ids=[30], page=1, page_size=half_limit)

    tasks = (tasks_29 or []) + (tasks_30 or [])
    stats["fetched"] = len(tasks)
    print(f"Получено {len(tasks)} задач (29: {len(tasks_29)}, 30: {len(tasks_30)}) для анализа качества...")

    for t in tasks:
        t_id = t.get("Id")
        full_task = await is_client.get_task_details(t_id)
        if not full_task:
            continue

        comments = full_task.get("Comments") or []
        if not comments:
            comments = await is_client.get_task_history(t_id)

        is_worthy, reason, entry = smart_filter_task(full_task, comments)

        if not is_worthy:
            stats["skipped"] += 1
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            continue

        stats["accepted"] += 1
        print(f"✓ [#{t_id}] Одобрен: {entry['original_name'][:40]}... ({entry['status_name']})")

        if not dry_run:
            ok, storage_label = await index_task_record(
                task_id=entry["task_id"],
                original_name=entry["original_name"],
                problem=entry["problem"],
                solution=entry["solution"],
                service_id=entry["service_id"],
                service_name=entry["service_name"],
                status_name=entry["status_name"],
                classification_data=entry["classification_data"],
            )
            if ok:
                stats["indexed"] += 1
                if "PostgreSQL" in storage_label:
                    stats["storage_breakdown"]["pgvector"] += 1
                else:
                    stats["storage_breakdown"]["local_cache"] += 1

    return stats
