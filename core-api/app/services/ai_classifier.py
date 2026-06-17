import asyncio
import os
import json
import logging
from typing import Any
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
import chromadb

from app.config import settings
from app.services.worker import get_redis_client

logger = logging.getLogger(__name__)

class ClassifierResult(BaseModel):
    action: str = Field(description="Действие: 'none' (если заявка создана в правильном разделе и ее не нужно отменять), 'redirect' (если заявка создана в неверном разделе и ее нужно отменить и перенаправить)")
    correct_service_id: int = Field(description="ID правильного сервиса (раздела) из каталога услуг, куда нужно перенаправить заявку. Если перенаправление не требуется, укажи -1")
    correct_service_name: str = Field(description="Точное название правильного сервиса (раздела) из каталога услуг. Если перенаправление не требуется, укажи пустую строку")
    comment_text: str = Field(description="Текст комментария для пользователя на русском языке с вежливым объяснением, почему заявка отменяется и в каком разделе ее нужно пересоздать. Не используй приветствия и подписи, пиши строго по делу.")
    reason: str = Field(description="Краткое обоснование принятого решения (для логирования)")


class AIClassifier:
    def __init__(self):
        self.litellm_key = os.getenv("LITELLM_API_KEY", "sk-intraservice-master-key")
        self.litellm_base_url = os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1")
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        
        # Определение пути к ChromaDB
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        # Путь от core-api/app/services/ к core-api/chroma_db
        self.chroma_path = os.path.abspath(os.path.join(self.script_dir, "..", "..", "chroma_db"))
        
        self.chroma_client = None
        self.collection = None
        
        try:
            self.chroma_client = chromadb.PersistentClient(path=self.chroma_path)
            self.collection = self.chroma_client.get_or_create_collection(name="intraservice_kb")
            logger.info("ChromaDB успешно инициализирована в классификаторе по пути: %s", self.chroma_path)
        except Exception as e:
            logger.error("Ошибка при инициализации ChromaDB в классификаторе: %s", e)
            
        self.llm_client = AsyncOpenAI(api_key=self.litellm_key, base_url=self.litellm_base_url)

    async def get_similar_cases(self, name: str, description: str, limit: int = 3) -> str:
        """
        Ищет похожие заявки в ChromaDB и форматирует их для промпта.
        """
        if not self.collection:
            return "База знаний RAG временно недоступна."
            
        query_text = f"Тема: {name}\nОписание: {description}"
        try:
            # Асинхронный запуск синхронного query метода ChromaDB в executor
            loop = asyncio.get_running_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self.collection.query(
                    query_texts=[query_text],
                    n_results=limit
                )
            )
            
            if not results or not results.get("documents") or not results["documents"][0]:
                return "Похожих кейсов не найдено в базе знаний."
                
            formatted_cases = []
            for doc, metadata in zip(results["documents"][0], results["metadatas"][0]):
                task_id = metadata.get("task_id", "Unknown")
                svc_name = metadata.get("service_name", "Unknown")
                status = metadata.get("status_name", "Unknown")
                formatted_cases.append(
                    f"Кейс #{task_id} (Раздел: {svc_name}, Статус: {status}):\n{doc}\n"
                )
            return "\n".join(formatted_cases)
        except Exception as e:
            logger.error("Ошибка при поиске похожих кейсов в ChromaDB: %s", e)
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
        
        # 1. Получаем каталог услуг из Redis
        redis = get_redis_client()
        catalog_str = ""
        try:
            catalog_json = await redis.get("worker:service_catalog")
            if catalog_json:
                catalog = json.loads(catalog_json)
                # Форматируем каталог услуг
                catalog_lines = []
                for svc in catalog:
                    catalog_lines.append(f"- ID: {svc.get('id')}, Название: \"{svc.get('name')}\"")
                catalog_str = "\n".join(catalog_lines)
            else:
                logger.warning("Каталог услуг отсутствует в Redis. Запускаем классификацию без полного каталога.")
        except Exception as e:
            logger.error("Ошибка при получении каталога услуг из Redis: %s", e)

        # 2. Получаем похожие кейсы из ChromaDB RAG
        similar_cases = await self.get_similar_cases(name, description)

        # 3. Формируем промпт для LLM
        prompt = f"""
Ты — интеллектуальный ассистент службы поддержки (AI Classifier).
Твоя задача — проанализировать новую входящую заявку и определить, правильно ли пользователь выбрал раздел (сервис) в каталоге услуг IntraService.

Текущая заявка:
- ID заявки: {task.get('Id')}
- Тема: {name}
- Описание: {description}
- Выбранный раздел (сервис): "{service_name}" (ID: {service_id})

Похожие исторические кейсы из базы знаний:
{similar_cases}

Каталог всех доступных услуг (разделов):
{catalog_str}

ИНСТРУКЦИЯ ПО ПРИНЯТИЮ РЕШЕНИЯ:
1. Сравни тему и описание заявки с выбранным разделом.
2. Изучи похожие исторические кейсы. Обрати особое внимание на кейсы со статусом "Отменена": если там похожая проблема приводила к отмене и перенаправлению в другой раздел, следуй этой логике.
3. Если заявка подана в правильный раздел (например, заявка о создании почты подана в раздел "Создание электронной почты"), то:
   - action = "none"
   - correct_service_id = -1
   - correct_service_name = ""
   - comment_text = ""
4. Если заявка подана в НЕВЕРНЫЙ раздел (например, восстановление пароля от почты подано в "Создание электронной почты" вместо "Разблокировка электронной почты" или "Информационная безопасность"):
   - Найди в каталоге наиболее подходящий раздел для этой проблемы.
   - Установи action = "redirect".
   - Укажи ID и точное Название правильного раздела из каталога.
   - Сформулируй вежливый и понятный комментарий пользователю на русском языке. В комментарии укажи, что заявка отменена, так как создана не в том разделе, и напиши точное название раздела, в котором ее нужно пересоздать. Не используй приветствий ("Здравствуйте", "Добрый день") и подписей ("С уважением", "Служба поддержки"). Сразу пиши суть.

Примеры хороших комментариев:
"Заявка отменена, так как создана в неверном разделе. Пожалуйста, пересоздайте заявку в разделе '08. Информационная безопасность -> Разблокировка электронной почты'."
"Заявка создана не в том разделе каталога услуг. Для решения этого вопроса требуется оставить новую заявку в разделе 'Учетные записи пользователей -> Изменение прав доступа'."
"""

        try:
            # Вызов LLM для структурированного вывода
            response = await self.llm_client.beta.chat.completions.parse(
                model=self.model_name,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                response_format=ClassifierResult,
                temperature=0.1,
            )
            
            result = response.choices[0].message.parsed
            logger.info("Результат классификации заявки #%s: %s (Reason: %s)", task.get("Id"), result.action, result.reason)
            return result
            
        except Exception as e:
            logger.error("Ошибка при классификации заявки #%s через LLM: %s", task.get("Id"), e)
            # В случае ошибки возвращаем безопасный результат "none"
            return ClassifierResult(
                action="none",
                correct_service_id=-1,
                correct_service_name="",
                comment_text="",
                reason=f"Ошибка классификации: {e}"
            )
