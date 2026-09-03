"""
Модуль семантической классификации регламентов Helpdesk на базе эмбеддингов FastEmbed.
Заменяет хрупкие регулярные выражения векторными прототипами (Semantic Rule Anchors).
Работает за ~15 мс на CPU без обращения к внешним LLM и без расхода токенов.
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger("core_api.semantic_classifier")

# Семантические прототипы для корпоративных регламентов
RULE_PROTOTYPES: dict[str, list[str]] = {
    "hardware_repair": [
        "компьютер не включается, системный блок шумит пищит запах гари искрит",
        "залили ноутбук жидкостью чаем кофе разбит экран монитора треснул корпус",
        "замена жесткого диска ssd видеокарты блока питания оперативной памяти кулера термопасты",
        "аппаратная диагностика оборудования в сервисном центре принести системник на ремонт",
        "синий экран смерти bsod зависает намертво выключается сам при нагрузке",
        "физическая поломка пк сломалась кнопка включения поврежден разъем питания",
    ],
    "bring_device_112": [
        "принесу к вам устройство компьютер привезем системный блок в кабинет 112",
        "согласование времени визита в техподдержку со сломанной техникой",
        "сдать системный блок на диагностику в 112 каб",
    ],
    "1c_cache": [
        "ошибка 1С формат потока неверный формат данных очистить кэш 1с",
        "база 1С зависла при запуске аварийное завершение сеанса 1С Предприятие",
        "1С вылетает с ошибкой при открытии информационной базы",
    ],
    "wlan_access": [
        "доступ к корпоративной сети wifi подключить телефон планшет к вайфай wlan worknet",
        "заявка на беспроводную корпоративную сеть для сотрудника",
        "предоставить пароль и доступ к рабочей сети Wi-Fi",
    ],
    "credentials_reset": [
        "забыл пароль от компьютера заблокирована учетная запись в домене Windows",
        "сбросить доменный пароль для входа в учетку",
        "разблокировать пользователя в Active Directory",
    ],
    "printer_spooler": [
        "принтер не печатает застряла бумага очередь печати зависла ошибка печати",
        "документ висит в очереди на печать МФУ не реагирует",
        "перезапустить службу диспетчера печати print spooler",
    ],
}

# Кэш векторов-прототипов: intent -> list[list[float]]
_ANCHOR_VECTORS: dict[str, list[list[float]]] = {}
_INITIALIZED = False


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


def ensure_anchors_initialized() -> None:
    """Инициализирует векторы-прототипы через FastEmbed в памяти (однократно)."""
    global _ANCHOR_VECTORS, _INITIALIZED
    if _INITIALIZED:
        return

    try:
        from app.services.rag import _get_fastembed_vector_sync
    except Exception:
        return

    for intent, texts in RULE_PROTOTYPES.items():
        vectors = []
        for text in texts:
            try:
                vec = _get_fastembed_vector_sync(text)
                if vec:
                    vectors.append(vec)
            except Exception:
                pass
        if vectors:
            _ANCHOR_VECTORS[intent] = vectors

    if _ANCHOR_VECTORS:
        _INITIALIZED = True
        logger.info("Семантические якоря регламентов инициализированы: %s категорий", len(_ANCHOR_VECTORS))


def classify_semantic_intent(
    text: str,
    threshold: float = 0.75,
) -> tuple[str | None, float]:
    """
    Классифицирует текст заявки по семантическим якорям.
    Возвращает (intent_name, score) или (None, 0.0) при уверенности ниже порога.
    """
    if not text or len(text.strip()) < 5:
        return None, 0.0

    ensure_anchors_initialized()
    if not _ANCHOR_VECTORS:
        return None, 0.0

    try:
        from app.services.rag import _get_fastembed_vector_sync
        query_vec = _get_fastembed_vector_sync(text)
        if not query_vec:
            return None, 0.0
    except Exception:
        return None, 0.0

    best_intent: str | None = None
    best_score: float = 0.0

    for intent, vectors in _ANCHOR_VECTORS.items():
        for anchor_vec in vectors:
            sim = _cosine_similarity(query_vec, anchor_vec)
            if sim > best_score:
                best_score = sim
                best_intent = intent

    if best_score >= threshold:
        return best_intent, round(best_score, 3)

    return None, round(best_score, 3)
