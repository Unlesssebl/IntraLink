import asyncio
import os
import json
import logging
from typing import Any
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from sqlalchemy import select

from core.config import settings
from services.redis_client import get_redis_client
from core.db import AsyncSessionLocal, TaskKnowledgeBase
from services.embeddings import get_embedding

logger = logging.getLogger(__name__)

class ClassifierResult(BaseModel):
    action: str = Field(description="Действие: 'none' (если заявка создана в правильном разделе и ее не нужно отменять), 'redirect' (если заявка создана в явно неверном разделе и ее нужно отменить и перенаправить)")
    confidence: float = Field(description="Уверенность в решении от 0.0 до 1.0. При перенаправлении (redirect) должна быть не ниже 0.8, иначе выбери 'none'.")
    correct_service_id: int = Field(description="ID правильного сервиса (раздела) из каталога услуг, куда нужно перенаправить заявку. Если перенаправление не требуется, укажи -1")
    correct_service_name: str = Field(description="Точное название правильного сервиса (раздела) из каталога услуг. Если перенаправление не требуется, укажи пустую строку")
    comment_text: str = Field(description="Текст комментария для пользователя на русском языке с вежливым объяснением, почему заявка отменяется и в каком разделе ее нужно пересоздать. Не используй приветствия и подписи, пиши строго по делу.")
    reason: str = Field(description="Краткое обоснование принятого решения (для логирования)")


class AIClassifier:
    def __init__(self):
        self.litellm_key = settings.LITELLM_API_KEY
        self.litellm_base_url = settings.LITELLM_BASE_URL
        self.model_name = settings.GEMINI_MODEL
        
        # Порог косинусного расстояния для RAG (по умолчанию 0.4, настраивается через env)
        # Чем меньше значение, тем жестче отбор (0.0 - идеальное сходство, 1.0 - ортогональные векторы)
        self.distance_threshold = float(os.getenv("RAG_DISTANCE_THRESHOLD", "0.4"))
        
        self.llm_client = AsyncOpenAI(api_key=self.litellm_key, base_url=self.litellm_base_url)

    async def get_similar_cases(self, name: str, description: str, limit: int = 3) -> str:
        """
        Ищет похожие заявки в PostgreSQL (pgvector) по косинусному сходству и форматирует их для промпта.
        """
        query_text = f"Тема: {name}\nОписание: {description}"
        try:
            # Генерация вектора запроса
            query_vector = await get_embedding(query_text)
            
            # Поиск в базе по косинусному расстоянию
            async with AsyncSessionLocal() as session:
                distance_expr = TaskKnowledgeBase.embedding.cosine_distance(query_vector)
                stmt = (
                    select(TaskKnowledgeBase, distance_expr.label("distance"))
                    .order_by("distance")
                    .limit(limit)
                )
                result = await session.execute(stmt)
                rows = result.all()
                
            if not rows:
                return "Похожих кейсов не найдено в базе знаний."
                
            formatted_cases = []
            for row in rows:
                case: TaskKnowledgeBase = row[0]
                distance: float = row[1]
                
                # Фильтруем нерелевантные кейсы (если расстояние больше порогового)
                if distance > self.distance_threshold:
                    logger.info("Кейс #%s отсечен по расстоянию: %.4f > %.4f", case.task_id, distance, self.distance_threshold)
                    continue
                    
                equipment = case.classification_data.get("equipment_type", "Unknown")
                action = case.classification_data.get("action_type", "Unknown")
                tags = case.classification_data.get("tags", [])
                
                formatted_cases.append(
                    f"Кейс #{case.task_id} (Раздел: {case.service_name}, Статус: {case.status_name}, Расстояние: {distance:.4f}):\n"
                    f"Проблема: {case.problem}\n"
                    f"Решение: {case.solution}\n"
                    f"Классификация: Оборудование={equipment}, Действие={action}, Теги={tags}\n"
                )
                
            if not formatted_cases:
                return "Похожих кейсов высокой релевантности не найдено в базе знаний."
                
            return "\n".join(formatted_cases)
        except Exception as e:
            logger.error("Ошибка при поиске похожих кейсов в pgvector: %s", e)
            return "Ошибка при поиске похожих кейсов."

    async def classify_task(self, task: dict) -> ClassifierResult:
        """
        Классифицирует задачу: проверяет, создана ли она в правильном разделе.
        Возвращает ClassifierResult.
        """
        name = task.get("Name", "")
        description = task.get("Description", "")
        service_name = task.get("ServiceName", "")
        service_id = task.get("ServiceId")
        
        # 1. Получаем каталог услуг из Redis и строим иерархическое дерево
        redis = get_redis_client()
        catalog_str = ""
        full_service_path = service_name
        try:
            catalog_json = await redis.get("worker:service_catalog")
            if catalog_json:
                catalog = json.loads(catalog_json)
                svc_map = {svc["id"]: svc for svc in catalog}

                def get_full_path(svc_id):
                    parts = []
                    cur = svc_map.get(svc_id)
                    while cur:
                        parts.insert(0, cur["name"])
                        parent_id = cur.get("parent_id")
                        cur = svc_map.get(parent_id) if parent_id else None
                    return " -> ".join(parts)

                # Восстанавливаем название выбранной услуги и строим полный путь
                if service_id and service_id in svc_map:
                    if not service_name:
                        service_name = svc_map[service_id]["name"]
                    full_service_path = get_full_path(service_id)

                catalog_lines = []
                for svc in catalog:
                    full_path = get_full_path(svc["id"])
                    catalog_lines.append(f"- ID: {svc['id']} | Путь: {full_path}")
                catalog_str = "\n".join(catalog_lines)
            else:
                logger.warning("Каталог услуг отсутствует в Redis. Запускаем классификацию без полного каталога.")
        except Exception as e:
            logger.error("Ошибка при получении каталога услуг из Redis: %s", e)

        # 2. Получаем похожие кейсы из pgvector RAG
        similar_cases = await self.get_similar_cases(name, description)

        # 3. Формируем промпт для LLM
        prompt = f"""
Ты — строгий классификатор заявок службы поддержки (AI Classifier).
Твоя единственная задача — определить, создана ли заявка в ЯВНО НЕПРАВИЛЬНОМ разделе.

Текущая заявка:
- ID: {task.get('Id')}
- Тема: {name}
- Описание: {description}
- Выбранный раздел: "{full_service_path}" (ID: {service_id})

Похожие исторические кейсы из базы знаний:
{similar_cases}

Каталог услуг (ID | Полный иерархический путь раздела):
{catalog_str}

ПРАВИЛА ПРИНЯТИЯ РЕШЕНИЯ (соблюдай строго):

ПРАВИЛО 1 — ПРЕЗУМПЦИЯ ПРАВОТЫ ПОЛЬЗОВАТЕЛЯ.
По умолчанию считай, что раздел выбран верно. Перенаправляй ТОЛЬКО если раздел ЯВНО неверный.
Если есть хоть малейшие сомнения — выбирай action = "none".

ПРАВИЛО 2 — НЕ перенаправляй между схожими или иерархически связанными разделами.
Если текущий раздел является родительским, дочерним, или смежным разделом для более подходящего — это НЕ повод для перенаправления.
Примеры ситуаций, когда НЕ нужно перенаправлять:
  - Выбран общий раздел вместо более специфичного подраздела (например, "Ремонт" вместо "МФУ и принтеры")
  - Выбран смежный подраздел в той же категории ("Монтаж оптики" вместо "Монтаж Сети")
  - Выбрана родительская категория оборудования ("Компьютеры и ноутбуки" для проблемы с монитором)
  - Выбран подходящий, но не идеально точный раздел

ПРАВИЛО 3 — Перенаправляй только при ОЧЕВИДНОМ несоответствии тематики.
Перенаправление уместно только если тема заявки категорически НЕ относится к выбранному разделу и целевая аудитория раздела другая.
Примеры ОЧЕВИДНЫХ ошибок (только тогда redirect):
  - Заявка про 1С создана в разделе "Периферия" или "Сеть"
  - Заявка на установку принтера создана в разделе "Учетные записи"
  - Заявка по доступу к папке создана в разделе "Оборудование"
  - Заявка явно про другую систему или другой тип услуг

ПРАВИЛО 4 — УВЕРЕННОСТЬ.
Устанавливай confidence >= 0.8 ТОЛЬКО если ты абсолютно уверен в очевидной ошибке.
If confidence < 0.8 — ОБЯЗАТЕЛЬНО выбирай action = "none".

Если action = "none":
  - correct_service_id = -1
  - correct_service_name = ""
  - comment_text = ""

Если action = "redirect" (только при очевидной ошибке и confidence >= 0.8):
  - Укажи ID и Название правильного раздела из каталога
  - Напиши вежливый комментарий пользователю БЕЗ приветствий и подписей
"""

        try:
            # Вызов LLM для структурированного вывода
            response = await self.llm_client.beta.chat.completions.parse(
                model=self.model_name,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                response_format=ClassifierResult,
                temperature=0.0,
            )
            
            result = response.choices[0].message.parsed
            if result is None:
                raise ValueError("Не удалось распарсить ответ LLM в формат ClassifierResult")

            # Применяем порог уверенности: redirect только при confidence >= 0.8
            if result.action == "redirect" and result.confidence < 0.8:
                logger.info(
                    "Заявка #%s: понижена с redirect до none (confidence=%.2f < 0.8)",
                    task.get("Id"), result.confidence
                )
                result.action = "none"
                result.correct_service_id = -1
                result.correct_service_name = ""
                result.comment_text = ""

            logger.info(
                "Результат классификации заявки #%s: %s (confidence=%.2f, Reason: %s)",
                task.get("Id"), result.action, result.confidence, result.reason
            )
            return result
            
        except Exception as e:
            logger.error("Ошибка при классификации заявки #%s через LLM: %s", task.get("Id"), e)
            # В случае ошибки возвращаем безопасный результат "none"
            return ClassifierResult(
                action="none",
                confidence=0.0,
                correct_service_id=-1,
                correct_service_name="",
                comment_text="",
                reason=f"Ошибка классификации: {e}"
            )
