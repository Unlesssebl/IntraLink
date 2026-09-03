"""
Сервис потоковой детекции массовых инцидентов и аварий (Real-time Outage Detection).
Выполняет векторную кластеризацию входящих заявок (FastEmbed + косинусное сходство)
и объединяет взаимосвязанные сбои в единые Master-инциденты.
"""

import asyncio
import json
import logging
import math
import re
import time
from typing import Any

from app.services.rag import _get_fastembed_vector_sync, clean_html
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.services.outage_detector")

OUTAGE_TRIGGER_WORDS = [
    "не работает",
    "не открывается",
    "у всех",
    "весь отдел",
    "упал",
    "упала",
    "упали",
    "сбой",
    "зависло",
    "завис",
    "ошибка",
    "недоступен",
    "недоступна",
    "нет сети",
    "нет интернета",
    "пропал интернет",
    "1с вылетает",
    "база заблокирована",
    "отключился",
    "массово",
]

_embedding_cache: dict[int, list[float]] = {}


def cosine_similarity(v1: list[float] | None, v2: list[float] | None) -> float:
    """Вычисляет косинусное сходство между двумя векторами."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot / (norm1 * norm2)


def has_outage_triggers(text: str) -> bool:
    """Проверяет наличие маркеров аварийного характера в тексте."""
    lower = text.lower()
    return any(word in lower for word in OUTAGE_TRIGGER_WORDS)


class OutageDetector:
    """
    Потоковый детектор массовых инцидентов инфраструктуры.
    """

    @classmethod
    async def get_ticket_embedding(cls, task_id: int, text: str) -> list[float] | None:
        """Получает или кэширует векторный эмбеддинг для текста заявки."""
        if task_id in _embedding_cache:
            return _embedding_cache[task_id]

        cleaned = clean_html(text).strip()
        if not cleaned:
            return None

        # Ограничиваем длину для оптимизации инференса
        truncated = cleaned[:512]
        vec = await asyncio.to_thread(_get_fastembed_vector_sync, truncated)
        if vec:
            if len(_embedding_cache) > 200:
                _embedding_cache.clear()
            _embedding_cache[task_id] = vec
        return vec

    @classmethod
    async def detect_outages(
        cls, tickets: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Анализирует список заявок и выявляет кластеры массовых инцидентов.
        Возвращает список обнаруженных активных аварий.
        """
        if len(tickets) < 2:
            return await cls.get_active_outages()

        # 1. Формируем векторные представления заявок
        prepared_tickets = []
        for t in tickets:
            task_id = t.get("id") or t.get("Id")
            if not task_id:
                continue
            try:
                task_id = int(task_id)
            except (ValueError, TypeError):
                continue

            title = t.get("name") or t.get("Name") or t.get("title") or ""
            desc = t.get("description") or t.get("Description") or ""
            service_id = t.get("service_id") or t.get("ServiceId")
            service_name = (
                t.get("service_name")
                or t.get("ServiceName")
                or t.get("service")
                or "Общий сервис"
            )
            created = t.get("created") or t.get("Created")

            combined_text = f"{title}. {desc}"
            emb = await cls.get_ticket_embedding(task_id, combined_text)

            prepared_tickets.append({
                "id": task_id,
                "title": title,
                "text": combined_text,
                "service_id": service_id,
                "service_name": service_name,
                "created": created,
                "embedding": emb,
                "has_trigger": has_outage_triggers(combined_text),
            })

        # 2. Графовая кластеризация по косинусному сходству
        clusters: list[list[dict[str, Any]]] = []
        visited = set()

        for i, t1 in enumerate(prepared_tickets):
            if t1["id"] in visited:
                continue

            current_cluster = [t1]
            visited.add(t1["id"])

            for j, t2 in enumerate(prepared_tickets):
                if i == j or t2["id"] in visited:
                    continue

                # Проверка семантического сходства векторов
                sim = (
                    cosine_similarity(t1["embedding"], t2["embedding"])
                    if t1["embedding"] and t2["embedding"]
                    else 0.0
                )

                same_service = (
                    t1["service_id"] is not None
                    and t1["service_id"] == t2["service_id"]
                )
                
                # Условия объединения в инцидент:
                # А) Высокое семантическое сходство векторов (>= 0.78)
                # Б) Одинаковый сервис + сходство >= 0.65 + наличие аварийных маркеров
                is_related = False
                if sim >= 0.78:
                    is_related = True
                elif same_service and sim >= 0.65 and (t1["has_trigger"] or t2["has_trigger"]):
                    is_related = True
                elif same_service and (t1["has_trigger"] and t2["has_trigger"]):
                    is_related = True

                if is_related:
                    current_cluster.append(t2)
                    visited.add(t2["id"])

            # Порог фиксации массового инцидента: от 3 заявок (или от 2 при явных триггерах "у всех / массово")
            has_explicit_mass = any(
                ("у всех" in c["text"].lower() or "весь отдел" in c["text"].lower() or "массово" in c["text"].lower())
                for c in current_cluster
            )
            if len(current_cluster) >= 3 or (len(current_cluster) >= 2 and has_explicit_mass):
                clusters.append(current_cluster)

        # 3. Сохранение выявленных аварий в Redis
        new_outages = []
        for cluster in clusters:
            outage = await cls._persist_cluster(cluster)
            if outage:
                new_outages.append(outage)

        return await cls.get_active_outages()

    @classmethod
    async def _persist_cluster(
        cls, cluster: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Формирует и сохраняет запись инцидента в Redis."""
        ticket_ids = sorted([c["id"] for c in cluster])
        master_ticket_id = ticket_ids[0]
        service_id = cluster[0].get("service_id")
        service_name = cluster[0].get("service_name") or "ИТ-инфраструктура"

        # Формируем стабильный ID аварии
        outage_id = f"outage:{service_id or 'general'}:{master_ticket_id}"

        # Гипотеза первопричины на основе тем заявок
        titles = [c["title"] for c in cluster if c["title"]]
        lead_title = titles[0] if titles else "Массовая недоступность сервиса"
        root_cause = f"Группа из {len(ticket_ids)} обращений по сбою '{lead_title}'"

        # Уровень критичности
        is_critical = (
            len(ticket_ids) >= 5
            or any("1с" in t.lower() or "сеть" in t.lower() or "интернет" in t.lower() for t in titles)
        )
        severity = "critical" if is_critical else "warning"

        outage_data = {
            "id": outage_id,
            "title": f"Массовый инцидент: {service_name} ({len(ticket_ids)} заявок)",
            "service_id": service_id,
            "service_name": service_name,
            "severity": severity,
            "status": "active",
            "master_ticket_id": master_ticket_id,
            "ticket_ids": ticket_ids,
            "detected_at": time.time(),
            "updated_at": time.time(),
            "root_cause_hypothesis": root_cause,
            "sample_titles": titles[:4],
        }

        try:
            r = get_redis_client()
            key = f"outage:active:{outage_id}"
            await r.set(key, json.dumps(outage_data, ensure_ascii=False), ex=7200)
            await r.sadd("outages:active_set", outage_id)

            # Публикуем событие в шину SSE
            sse_payload = {
                "event": "outage_detected",
                "outage": outage_data,
                "timestamp": time.time(),
            }
            await r.publish("events:all", json.dumps(sse_payload, ensure_ascii=False))

            logger.info(
                "🚨 Выявлен массовый сбой %s: %s (заявки: %s)",
                outage_id,
                outage_data["title"],
                ticket_ids,
            )
            return outage_data
        except Exception as e:
            logger.error("Ошибка сохранения инцидента в Redis: %s", e)
            return None

    @classmethod
    async def get_active_outages(cls) -> list[dict[str, Any]]:
        """Возвращает все активные зарегистрированные аварии из Redis."""
        try:
            r = get_redis_client()
            outage_ids = await r.smembers("outages:active_set")
            if not outage_ids:
                return []

            outages = []
            for oid in outage_ids:
                if isinstance(oid, bytes):
                    oid = oid.decode("utf-8")
                raw = await r.get(f"outage:active:{oid}")
                if raw:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    try:
                        outages.append(json.loads(raw))
                    except Exception:
                        pass
                else:
                    # Устаревший ключ
                    await r.srem("outages:active_set", oid)

            # Сортируем по критичности и свежести
            outages.sort(
                key=lambda x: (x.get("severity") == "critical", x.get("detected_at", 0)),
                reverse=True,
            )
            return outages
        except Exception as e:
            logger.debug("Ошибка получения активных аварий из Redis: %s", e)
            return []

    @classmethod
    async def resolve_outage(cls, outage_id: str, operator: str = "operator") -> bool:
        """Снимает инцидент из активных и уведомляет клиентов по SSE."""
        try:
            r = get_redis_client()
            key = f"outage:active:{outage_id}"
            raw = await r.get(key)
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
                data["status"] = "resolved"
                data["resolved_by"] = operator
                data["resolved_at"] = time.time()
                # Переносим в архивный ключ на 24 часа
                await r.set(f"outage:archive:{outage_id}", json.dumps(data, ensure_ascii=False), ex=86400)
            
            await r.delete(key)
            await r.srem("outages:active_set", outage_id)

            # Оповещаем SSE клиентов о снятии аварии
            sse_payload = {
                "event": "outage_resolved",
                "outage_id": outage_id,
                "operator": operator,
                "timestamp": time.time(),
            }
            await r.publish("events:all", json.dumps(sse_payload, ensure_ascii=False))
            logger.info("✅ Инцидент %s снят оператором %s", outage_id, operator)
            return True
        except Exception as e:
            logger.error("Ошибка при снятии инцидента %s: %s", outage_id, e)
            return False
