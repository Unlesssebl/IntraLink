"""RAG-Augmented Semantic Prototype Index for IntraLink v2 Autopilot Router.

Computes and caches dense vector representations of scenario exemplar phrases
(prototypes). At routing time, computes cosine similarity between the incoming
ticket text and each scenario's prototype cluster, returning a per-scenario
semantic affinity score ∈ [0.0, 1.0].
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from core.rag.embedder import get_embedding_vector

# LiteLLM Gateway endpoint configured from environment
_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://litellm:4000/v1")
_LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "sk-intraservice-master-key")

logger = logging.getLogger("core.scenarios.semantic_index")

# ---------------------------------------------------------------------------
# Scenario Prototypes
# ---------------------------------------------------------------------------
SCENARIO_PROTOTYPES: Dict[str, List[str]] = {
    "install_printer": [
        "не могу подключить принтер к компьютеру",
        "установить сетевой принтер на рабочей станции",
        "принтер не печатает, ошибка драйвера Canon Kyocera",
        "нужно настроить МФУ в офисе, не добавляется в Windows",
        "принтер недоступен, не отображается в сети",
    ],
    "grant_wlan": [
        "прошу предоставить доступ к корпоративному Wi-Fi WLAN-WORKNET",
        "нужно подключить ноутбук к беспроводной сети компании",
        "нет доступа к wifi в офисе, требуется добавить в группу",
        "хочу подключиться к вайфай, добавьте мою учетку",
        "беспроводная сеть не доступна для моего устройства",
    ],
    "offline_host": [
        "компьютер не включается, чёрный экран, нет питания",
        "рабочая станция не реагирует на нажатие кнопки питания",
        "ПК не загружается, гудит кулер но монитор пустой",
        "не могу запустить компьютер в кабинете, запах гари",
        "рабочее место полностью недоступно, компьютер мертвый",
    ],
    "service_redirect": [
        "заявка на установку программы 1С для бухгалтерии",
        "вопрос по системе Directum, договора и тендеры",
        "заказать канцелярию, прошу выдать бумагу и ручки",
        "нужен пропуск для посетителя в офис",
        "клининг не приходил, уборка помещения",
    ],
    "rag_consultation": [
        "не знаю куда обратиться, общий вопрос по работе системы",
        "не работает приложение, непонятная ошибка при запуске",
        "возникла нестандартная ситуация, нужна консультация",
        "медленно работает интернет, теряются пакеты",
        "вопрос по настройке рабочего места, не знаю к кому идти",
    ],
}


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticPrototypeIndex:
    """In-memory semantic index for scenario prototype matching."""

    def __init__(self, ai_client: Optional[AsyncOpenAI] = None) -> None:
        self._ai_client = ai_client
        self._prototype_vectors: Dict[str, List[List[float]]] = {}
        self._is_ready: bool = False

    def _get_ai_client(self) -> AsyncOpenAI:
        if self._ai_client is not None:
            return self._ai_client
        return AsyncOpenAI(base_url=_LITELLM_BASE_URL, api_key=_LITELLM_API_KEY)

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    async def warm_up(self, scenarios: Optional[Dict[str, Any]] = None) -> None:
        if self._is_ready:
            return

        prototypes_to_use: Dict[str, List[str]] = dict(SCENARIO_PROTOTYPES)
        if scenarios:
            for scen_key, scen in scenarios.items():
                proto_list = getattr(scen, "semantic_prototypes", None)
                if proto_list:
                    prototypes_to_use[scen_key] = proto_list

        total_prototypes = sum(len(v) for v in prototypes_to_use.values())
        logger.info(
            "SemanticPrototypeIndex: warming up %d prototypes for %d scenarios…",
            total_prototypes,
            len(prototypes_to_use),
        )

        success_count = 0
        ai = self._get_ai_client()
        for scenario_key, phrases in prototypes_to_use.items():
            vectors: List[List[float]] = []
            for phrase in phrases:
                vec = await get_embedding_vector(phrase, ai)
                if vec is not None:
                    vectors.append(vec)
                    success_count += 1
                else:
                    logger.warning(
                        "SemanticPrototypeIndex: failed to embed prototype for '%s': '%s'",
                        scenario_key,
                        phrase[:60],
                    )
            self._prototype_vectors[scenario_key] = vectors

        if success_count == 0:
            logger.warning(
                "SemanticPrototypeIndex: zero prototypes vectorised – "
                "semantic Factor E will be disabled (LiteLLM unavailable?)"
            )
        else:
            self._is_ready = True
            logger.info(
                "SemanticPrototypeIndex ready: %d/%d prototypes vectorised.",
                success_count,
                total_prototypes,
            )

    async def score(self, text: str) -> Dict[str, float]:
        if not self._is_ready or not text.strip():
            return {}

        query_vec = await get_embedding_vector(text, self._get_ai_client())
        if query_vec is None:
            logger.debug("SemanticPrototypeIndex: failed to embed query text; returning empty scores")
            return {}

        result: Dict[str, float] = {}
        for scenario_key, proto_vecs in self._prototype_vectors.items():
            if not proto_vecs:
                result[scenario_key] = 0.0
                continue
            similarities = [_cosine_similarity(query_vec, pv) for pv in proto_vecs]
            max_sim = max(similarities)
            result[scenario_key] = max(0.0, min(1.0, max_sim))

        return result

    def get_top_semantic_match(self, scores: Dict[str, float]) -> Optional[Tuple[str, float]]:
        if not scores:
            return None
        best = max(scores.items(), key=lambda kv: kv[1])
        return best if best[1] > 0.0 else None
