"""
Централизованный AI Hub для Core API (Ollama + LiteLLM / Gemini).
Включает семафор параллелизма, Redis-кэширование (Pre-Summarization) и Circuit Breaker.
"""
import asyncio
import json
import logging
from typing import Any, List, Optional
import aiohttp

from app.config import settings
from app.services.ai.schemas import (
    AIAnalysisResult,
    AIHealthResponse,
    TicketSummaryResult,
)
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.ai_hub")


class AIHub:
    """
    Фасад и диспетчер AI-инференса монорепозитория IntraLink.
    """

    def __init__(self):
        self.ollama_url = settings.OLLAMA_BASE_URL.rstrip("/")
        self.ollama_model = settings.OLLAMA_MODEL
        self.litellm_url = settings.LITELLM_BASE_URL.rstrip("/")
        self._semaphore = asyncio.Semaphore(settings.OLLAMA_NUM_PARALLEL)
        self._http_session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=settings.OLLAMA_TIMEOUT)
            connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
            self._http_session = aiohttp.ClientSession(
                timeout=timeout, connector=connector
            )
        return self._http_session

    async def close(self) -> None:
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

    async def is_ollama_available(self, timeout_sec: float = 1.5) -> bool:
        """Быстрая проверка доступности Ollama инференса."""
        url = f"{self.ollama_url}/api/tags"
        try:
            session = await self._get_session()
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout_sec)
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def is_litellm_available(self, timeout_sec: float = 1.5) -> bool:
        """Быстрая проверка доступности LiteLLM Proxy."""
        url = f"{self.litellm_url}/health"
        try:
            session = await self._get_session()
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout_sec)
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def get_health(self) -> AIHealthResponse:
        """Возвращает статус здоровья всех подключенных AI-бэкендов."""
        ollama_ok = await self.is_ollama_available()
        litellm_ok = await self.is_litellm_available()
        return AIHealthResponse(
            ollama_available=ollama_ok,
            ollama_url=self.ollama_url,
            ollama_model=self.ollama_model,
            litellm_available=litellm_ok,
            litellm_url=self.litellm_url,
        )

    async def summarize_task_history(
        self,
        task_id: int,
        task_name: str,
        task_desc: str,
        comments: List[dict[str, Any]],
        bypass_cache: bool = False,
    ) -> Optional[TicketSummaryResult]:
        """
        Формирует структурированную выжимку цепочки переписки заявки.
        Использует L2 Redis-кэш и семафор параллелизма.
        """
        cache_key = f"cache:ai:summary:{task_id}"

        # 1. Проверяем кэш, если не запрошен принудительный пересчет
        if not bypass_cache:
            try:
                r = get_redis_client()
                cached_val = await r.get(cache_key)
                if cached_val:
                    data = json.loads(cached_val)
                    return TicketSummaryResult.model_validate(data)
            except Exception as e:
                logger.debug("Промах кэша Redis для заявки #%s: %s", task_id, e)

        # 2. Проверка доступности Ollama
        if not await self.is_ollama_available():
            logger.warning("Ollama сервис недоступен на %s", self.ollama_url)
            return None

        # Формируем читаемый тред переписки
        thread_lines = [
            f"Заявка #{task_id}: {task_name}",
            f"Описание проблемы заявителя: {task_desc}",
            "\nХронология комментариев:",
        ]

        for idx, c in enumerate(comments, 1):
            author = c.get("UserName") or c.get("Creator") or "Участник"
            created = (c.get("Created") or "")[:16].replace("T", " ")
            text = (c.get("Text") or c.get("Comment") or "").strip()
            clean_text = (
                text.replace("<br>", "\n")
                .replace("<br/>", "\n")
                .replace("<p>", "")
                .replace("</p>", "\n")
            )
            if clean_text:
                thread_lines.append(f"{idx}. [{created}] {author}: {clean_text}")

        thread_text = "\n".join(thread_lines)

        system_prompt = (
            "Ты - опытный ведущий инженер технической поддержки Helpdesk. "
            "Твоя задача - проанализировать переписку по заявке и составить краткое, четкое резюме для передачи смены инженерам. "
            "Отвечай СТРОГО на русском языке в формате JSON по заданной схеме без лишнего текста и без markdown блоков."
        )
        user_prompt = f"Проанализируй следующую историю заявки и сделай выжимку:\n\n{thread_text}"

        url = f"{self.ollama_url}/api/chat"
        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": TicketSummaryResult.model_json_schema(),
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 512,
            },
        }

        # 3. Инференс с ограничением параллелизма через Semaphore
        async with self._semaphore:
            try:
                session = await self._get_session()
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error("Ошибка Ollama API: HTTP %s", resp.status)
                        return None
                    data = await resp.json()
                    content = data.get("message", {}).get("content", "")
                    parsed = json.loads(content)
                    result = TicketSummaryResult.model_validate(parsed)

                    # Сохраняем в кэш Redis на 24 часа
                    try:
                        r = get_redis_client()
                        await r.setex(
                            cache_key,
                            86400,
                            json.dumps(result.model_dump(), ensure_ascii=False),
                        )
                    except Exception as cache_err:
                        logger.warning(
                            "Не удалось закэшировать сводку в Redis: %s", cache_err
                        )

                    return result
            except Exception as e:
                logger.error(
                    "Сбой инференса Ollama при суммаризации заявки #%s: %s", task_id, e
                )
                return None

    async def pre_summarize_task(
        self,
        task_id: int,
        task_name: str,
        task_desc: str,
        comments: List[dict[str, Any]],
    ) -> None:
        """Фоновый запуск упреждающей суммаризации для заполнения кэша."""
        if len(comments) >= 2:
            asyncio.create_task(
                self.summarize_task_history(
                    task_id=task_id,
                    task_name=task_name,
                    task_desc=task_desc,
                    comments=comments,
                    bypass_cache=False,
                )
            )

    async def analyze_complex_task(
        self,
        task_id: int,
        task_name: str,
        task_desc: str,
    ) -> Optional[AIAnalysisResult]:
        """
        Глубокий семантический анализ нетиповой заявки с извлечением сущностей.
        """
        if not await self.is_ollama_available():
            return None

        system_prompt = (
            "Ты - AI ассистент технической поддержки Helpdesk 1-й линии. "
            "Проанализируй текст заявки, выдели суть проблемы, определи категорию инцидента "
            "и извлеки любые упомянутые имена компьютеров (например, NTEMW0144), моделей принтеров и имена сотрудников. "
            "Отвечай СТРОГО на русском языке в формате JSON по заданной схеме."
        )
        user_prompt = f"Заявка #{task_id}: {task_name}\nОписание: {task_desc}"

        url = f"{self.ollama_url}/api/chat"
        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": AIAnalysisResult.model_json_schema(),
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 512,
            },
        }

        async with self._semaphore:
            try:
                session = await self._get_session()
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error("Ошибка Ollama API: HTTP %s", resp.status)
                        return None
                    data = await resp.json()
                    content = data.get("message", {}).get("content", "")
                    parsed = json.loads(content)
                    return AIAnalysisResult.model_validate(parsed)
            except Exception as e:
                logger.error(
                    "Сбой инференса Ollama при анализе заявки #%s: %s", task_id, e
                )
                return None


# Синглтон AI Hub для использования в сервисах
ai_hub = AIHub()
