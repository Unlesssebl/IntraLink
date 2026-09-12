"""
Semantic Scenario Intent Matcher (Pass 1).
Использует FastEmbed BGE-M3 (1024 dim) для семантического подбора сценариев.
Работает строго в режимах: off, shadow, canary, on.
В режиме shadow сохраняет альтернативный маршрут для аудита, не влияя на детерминированное исполнение.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import math
import re
from typing import Any

from app.config import settings
from app.services.scenarios.base import ScenarioContext

logger = logging.getLogger("core_api.intent_matcher")

# Версионируемые прототипы и контрпримеры для семантического ранжирования сценариев
SCENARIO_PROTOTYPES_V1: dict[str, list[str]] = {
    "pc_performance": [
        "компьютер зависает и очень медленно работает",
        "тормозит система постоянная загрузка процессора и диска на 100 процентов",
        "ПК сильно тормозит не открываются программы все висит память забита",
        "компьютер долго загружается приложения зависают намертво",
        "производительность рабочей станции крайне низкая все зависает",
    ],
    "printer_print_failure": [
        "принтер не печатает документы отправленные на печать",
        "ошибка печати документ завис в очереди печати принтера",
        "МФУ зажевало бумагу мигает красным не реагирует на печать",
        "принтер перестал печатать не захватывает бумагу",
        "не выходят документы с принтера очередь печати заблокирована",
    ],
    "printer_scan_failure": [
        "не сканирует сетевой сканер МФУ ошибка сканирования",
        "ошибка сетевого сканирования в сетевую папку сканер не отвечает",
        "сканер МФУ выдает ошибку связи при попытке сканирования",
        "не работает сканирование с МФУ на рабочий компьютер",
    ],
    "peripheral_diagnostics": [
        "не работает мышь и клавиатура курсор замер на экране",
        "компьютер не видит подключенную флешку или внешний диск USB",
        "клавиатура не реагирует на нажатия клавиш мышка отключилась",
        "периферийное устройство не распознается операционной системой Windows",
        "диагностика сбоя работы мыши и клавиатуры",
    ],
    "peripheral_setup": [
        "подключить и настроить новую клавиатуру и мышь на рабочем месте",
        "установить веб-камеру и микрофон для видеоконференций",
        "подключение второго монитора и периферийного оборудования к системному блоку",
        "настройка подключения внешней периферии к рабочему ПК",
    ],
}

# Явные маркеры отрицания действий (инвариант 5: "не надо устанавливать" != "не печатает")
NEGATION_ACTION_PATTERNS = [
    re.compile(r"\bне\s+(?:надо|нужно|требуется|следует)\s+(?:устанавливать|ставить|подключать|переустанавливать)\b", re.IGNORECASE),
    re.compile(r"\b(?:установка|переустановка|подключение)\s+не\s+(?:нужн[ао]|требуется)\b", re.IGNORECASE),
    re.compile(r"\bне\s+отменяйте\s+заявку\b", re.IGNORECASE),
]

# Маркер локальных проблем приложений (не общей производительности ПК)
APP_SPECIFIC_SLOWDOWN_PATTERN = re.compile(
    r"\b(?:тормозит|зависает|висит|медленно\s+работает)\s+только\s+(?:1с|1c|браузер|почта|outlook)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class SemanticCandidate:
    scenario_key: str
    score: float
    runner_up_key: str | None = None
    runner_up_score: float = 0.0
    margin: float = 0.0
    is_ambiguous: bool = False
    reasons: list[str] = field(default_factory=list)
    raw_similarities: dict[str, float] = field(default_factory=dict)


_PROTOTYPE_VECTORS: dict[str, list[list[float]]] = {}
_INITIALIZED: bool = False
_LOCK = asyncio.Lock()


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def _deterministic_hash_vector(text: str, dim: int = 1024) -> list[float]:
    """Детерминированный n-gram hashing vectorizer (fallback при отсутствии FastEmbed)."""
    import hashlib

    words = re.findall(r"\w+", text.lower())
    if not words:
        return [0.0] * dim
    vec = [0.0] * dim
    for w in words:
        tokens = [w] + [w[i : i + 3] for i in range(max(1, len(w) - 2))]
        for tok in tokens:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16) % dim
            vec[h] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _get_vector_for_text(text: str) -> list[float] | None:
    """Получение вектора: FastEmbed BGE-M3 или детерминированный fallback."""
    dim = getattr(settings, "EMBEDDING_DIMENSION", 1024)
    try:
        from app.services.rag import _get_fastembed_vector_sync

        v = _get_fastembed_vector_sync(text)
        if v and len(v) == dim:
            return v
    except Exception:
        pass
    return _deterministic_hash_vector(text, dim=dim)


def _warmup_prototypes_sync() -> dict[str, list[list[float]]]:
    """Синхронный прогрев векторов прототипов (FastEmbed или детерминированный fallback)."""
    dim = getattr(settings, "EMBEDDING_DIMENSION", 1024)
    vectors: dict[str, list[list[float]]] = {}
    for scen_key, texts in SCENARIO_PROTOTYPES_V1.items():
        scen_vecs = []
        for text in texts:
            try:
                vec = _get_vector_for_text(text)
                if vec and len(vec) == dim:
                    scen_vecs.append(vec)
            except Exception as exc:
                logger.warning("Ошибка векторизации прототипа для %s: %s", scen_key, exc)
        if scen_vecs:
            vectors[scen_key] = scen_vecs
    return vectors


async def ensure_matcher_initialized() -> None:
    """Атомарная инициализация векторов прототипов в памяти процесса."""
    global _PROTOTYPE_VECTORS, _INITIALIZED
    if _INITIALIZED:
        return
    async with _LOCK:
        if _INITIALIZED:
            return
        loop = asyncio.get_running_loop()
        try:
            vectors = await loop.run_in_executor(None, _warmup_prototypes_sync)
            if vectors:
                _PROTOTYPE_VECTORS = vectors
                _INITIALIZED = True
                logger.info("Семантические прототипы сценариев прогреты: %s сценариев", len(_PROTOTYPE_VECTORS))
        except Exception as exc:
            logger.warning("Не удалось инициализировать семантические прототипы: %s", exc)


def _match_sync(text: str, allowed_scenarios: set[str] | None = None) -> SemanticCandidate | None:
    """Синхронное вычисление сходства текста с прототипами."""
    if not _PROTOTYPE_VECTORS:
        return None

    try:
        query_vec = _get_vector_for_text(text)
    except Exception as exc:
        logger.warning("Сбой векторизации запроса в intent_matcher: %s", exc)
        return None

    if not query_vec:
        return None

    # Проверка отрицаний действий
    lowered = text.lower()
    excluded_scenarios: set[str] = set()
    if any(p.search(lowered) for p in NEGATION_ACTION_PATTERNS):
        excluded_scenarios.add("peripheral_setup")
        excluded_scenarios.add("install_printer")

    if APP_SPECIFIC_SLOWDOWN_PATTERN.search(lowered):
        excluded_scenarios.add("pc_performance")

    scores: dict[str, float] = {}
    for scen_key, proto_vecs in _PROTOTYPE_VECTORS.items():
        if allowed_scenarios is not None and scen_key not in allowed_scenarios:
            continue
        if scen_key in excluded_scenarios:
            continue
        max_sim = max((_cosine_similarity(query_vec, pv) for pv in proto_vecs), default=0.0)
        scores[scen_key] = round(max_sim, 4)

    if not scores:
        return None

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_key, top_score = sorted_scores[0]
    runner_up_key, runner_up_score = sorted_scores[1] if len(sorted_scores) > 1 else (None, 0.0)
    margin = round(top_score - runner_up_score, 4)

    min_score = settings.SCENARIO_SEMANTIC_MIN_SCORE
    min_margin = settings.SCENARIO_SEMANTIC_MIN_MARGIN

    is_ambiguous = bool(runner_up_key and margin < min_margin)
    reasons = [f"semantic_similarity:{top_score:.3f}"]
    if is_ambiguous:
        reasons.append(f"ambiguous_with:{runner_up_key}:{runner_up_score:.3f}")

    return SemanticCandidate(
        scenario_key=top_key,
        score=top_score,
        runner_up_key=runner_up_key,
        runner_up_score=runner_up_score,
        margin=margin,
        is_ambiguous=is_ambiguous,
        reasons=reasons,
        raw_similarities=scores,
    )


class ScenarioIntentMatcher:
    """Асинхронный координатор семантического сопоставления сценариев."""

    @classmethod
    async def match(
        cls,
        context: ScenarioContext,
        allowed_scenarios: set[str] | None = None,
    ) -> SemanticCandidate | None:
        mode = (settings.SCENARIO_SEMANTIC_ROUTING_MODE or "shadow").lower()
        if mode == "off":
            return None

        # Учитываем порядок сообщений и актуальный комментарий (инвариант 6)
        task = context.task
        subject = str(task.get("Name") or task.get("subject") or "").strip()
        description = str(task.get("Description") or task.get("description") or "").strip()

        # Если есть свежие комментарии заявителя, добавляем их
        latest_comment = ""
        if context.comments:
            user_comments = [
                c.get("Comment") or c.get("text") or ""
                for c in context.comments
                if not c.get("IsPrivate") and not c.get("is_private")
            ]
            if user_comments:
                latest_comment = user_comments[-1].strip()

        text_to_match = f"{subject}. {description}"
        if latest_comment:
            text_to_match = f"{text_to_match}. Актуальный комментарий: {latest_comment}"

        await ensure_matcher_initialized()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _match_sync, text_to_match, allowed_scenarios)
