"""
Асинхронный клиент для локальной нейросети Ollama с поддержкой Structured JSON Schema.
"""
import json
import logging
import os
import time
from typing import Any, Optional, List
import aiohttp

from .schemas import TicketSummaryResult, AIAnalysisResult

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")


class OllamaClient:
    """
    Клиент для локального инференса Ollama с Pydantic JSON-схемами и защитой от сбоев.
    """

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def is_available(self, timeout_sec: float = 1.0) -> bool:
        """
        Быстрая проверка доступности сервиса Ollama.
        """
        url = f"{self.base_url}/api/tags"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def summarize_task_history(
        self,
        task_id: int,
        task_name: str,
        task_desc: str,
        comments: List[dict[str, Any]],
        timeout_sec: float = 20.0,
    ) -> Optional[TicketSummaryResult]:
        """
        Формирует структурированную выжимку цепочки комментариев инцидента.
        """
        if not await self.is_available():
            logger.warning("Ollama сервис недоступен на %s", self.base_url)
            return None

        # Формируем читаемый тред переписки
        thread_lines = [
            f"Заявка #{task_id}: {task_name}",
            f"Описание проблемы заявителя: {task_desc}",
            "\nХронология комментариев:"
        ]
        
        for idx, c in enumerate(comments, 1):
            author = c.get("UserName") or c.get("Creator") or "Участник"
            created = (c.get("Created") or "")[:16].replace("T", " ")
            text = (c.get("Text") or c.get("Comment") or "").strip()
            # Очищаем HTML теги из комментариев
            clean_text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<p>", "").replace("</p>", "\n")
            if clean_text:
                thread_lines.append(f"{idx}. [{created}] {author}: {clean_text}")

        thread_text = "\n".join(thread_lines)

        system_prompt = (
            "Ты - опытный ведущий инженер технической поддержки Helpdesk. "
            "Твоя задача - проанализировать переписку по заявке и составить краткое, четкое резюме для передачи смены инженерам. "
            "Отвечай СТРОГО на русском языке в формате JSON по заданной схеме без лишнего текста и без markdown блоков."
        )

        user_prompt = f"Проанализируй следующую историю заявки и сделай выжимку:\n\n{thread_text}"

        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "format": TicketSummaryResult.model_json_schema(),
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 512,
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
                    if resp.status != 200:
                        logger.error("Ошибка Ollama API: HTTP %s", resp.status)
                        return None
                    data = await resp.json()
                    content = data.get("message", {}).get("content", "")
                    parsed = json.loads(content)
                    return TicketSummaryResult.model_validate(parsed)
        except Exception as e:
            logger.error("Сбой инференса Ollama при суммаризации заявки #%s: %s", task_id, e)
            return None

    async def analyze_complex_task(
        self,
        task_id: int,
        task_name: str,
        task_desc: str,
        timeout_sec: float = 15.0,
    ) -> Optional[AIAnalysisResult]:
        """
        Глубокий анализ нетиповой или составной заявки.
        """
        if not await self.is_available():
            return None

        system_prompt = (
            "Ты - AI ассистент технической поддержки Helpdesk 1-й линии. "
            "Проанализируй текст заявки, выдели суть проблемы, определи категорию инцидента "
            "и извлеки любые упомянутые имена компьютеров (например, NTEMW0144), моделей принтеров и имена сотрудников. "
            "Отвечай СТРОГО на русском языке в формате JSON по заданной схеме."
        )
        user_prompt = f"Заявка #{task_id}: {task_name}\nОписание: {task_desc}"

        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "format": AIAnalysisResult.model_json_schema(),
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 512,
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    content = data.get("message", {}).get("content", "")
                    parsed = json.loads(content)
                    return AIAnalysisResult.model_validate(parsed)
        except Exception as e:
            logger.error("Сбой инференса Ollama при анализе заявки #%s: %s", task_id, e)
            return None
