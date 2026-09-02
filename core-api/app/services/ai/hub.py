"""
Централизованный AI Hub для Core API (Ollama + LiteLLM / Gemini).
Включает семафор параллелизма, Redis-кэширование (Pre-Summarization) и Circuit Breaker.
"""
import asyncio
import hashlib
import json
import logging
import time
import uuid
from typing import Any, List, Optional
import aiohttp

from app.config import settings
from app.services.ai.sanitizer import data_sanitizer
from app.services.ai.schemas import (
    AIAnalysisResult,
    AIHealthResponse,
    DataCircuit,
    RouteDecision,
    RoutedInferenceRequest,
    RoutedInferenceResponse,
    SanitizationResult,
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

    async def is_litellm_available(self, timeout_sec: float = 2.0) -> bool:
        """Быстрая проверка доступности LiteLLM Proxy."""
        base_url = self.litellm_url.removesuffix("/v1")
        url = f"{base_url}/health/liveliness"
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
                        await r.set(
                            cache_key,
                            json.dumps(result.model_dump(), ensure_ascii=False),
                            ex=86400,
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

    async def generate_ollama_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """Прямой инференс через локальную Ollama (Закрытый контур RED)."""
        if not await self.is_ollama_available():
            logger.warning("Локальная Ollama недоступна на %s", self.ollama_url)
            return None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        url = f"{self.ollama_url}/api/chat"
        payload = {
            "model": self.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with self._semaphore:
            try:
                session = await self._get_session()
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error("Ошибка локальной Ollama: HTTP %s", resp.status)
                        return None
                    data = await resp.json()
                    return data.get("message", {}).get("content", "").strip()
            except Exception as e:
                logger.error("Сбой локального инференса Ollama: %s", e)
                return None

    async def generate_cloud_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """Инференс строго через LiteLLM Proxy (Открытый контур GREEN/YELLOW с ротацией ключей)."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        url = f"{self.litellm_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.LITELLM_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.GEMINI_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        try:
            session = await self._get_session()
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    text_err = await resp.text()
                    logger.error(
                        "Ошибка Cloud LiteLLM/Gemini API: HTTP %s: %s",
                        resp.status,
                        text_err[:200],
                    )
                    return None
                data = await resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
                return None
        except Exception as e:
            logger.error("Сбой облачного инференса Gemini via LiteLLM: %s", e)
            return None

    async def dispatch_routed_inference(
        self, request: RoutedInferenceRequest
    ) -> Optional[RoutedInferenceResponse]:
        """
        Центральный умный диспетчер с защитой данных:
        1. Оценивает чувствительность (Red / Yellow / Green).
        2. При Red -> Строго локальная Ollama.
        3. При Yellow -> Десенсибилизация (PII Vault) -> Cloud Gemini -> Rehydration.
        4. При Green -> Cloud Gemini напрямую.
        5. Автоматическое L2 кэширование и замер времени выполнения.
        """
        start_time = time.perf_counter()

        # 1. Проверяем L2 кэш по хэшу промпта
        cache_hash = hashlib.sha256(
            f"{request.prompt}:{request.system_prompt}:{request.temperature}".encode()
        ).hexdigest()
        cache_key = f"cache:ai:routed:{cache_hash}"

        if not request.bypass_cache:
            try:
                r = get_redis_client()
                cached_val = await r.get(cache_key)
                if cached_val:
                    data = json.loads(cached_val)
                    data["cached"] = True
                    data["execution_time_ms"] = round(
                        (time.perf_counter() - start_time) * 1000, 2
                    )
                    return RoutedInferenceResponse.model_validate(data)
            except Exception as e:
                logger.debug("Промах кэша для routed AI: %s", e)

        # 2. Выполняем инспекцию и десенсибилизацию
        san_res = data_sanitizer.sanitize(request.prompt)
        decision = data_sanitizer.evaluate_circuit(
            prompt=request.prompt,
            metadata=request.metadata,
            sanitization_result=san_res,
        )

        model_name = (
            self.ollama_model
            if decision.circuit == DataCircuit.RED
            else settings.GEMINI_MODEL
        )
        sanitized_count = 0
        raw_output: Optional[str] = None

        # 3. Маршрутизация по контурам
        if decision.circuit == DataCircuit.RED:
            # ЗАКРЫТЫЙ КОНТУР: строго локальный инференс
            logger.info("Маршрутизация в ЗАКРЫТЫЙ контур (RED): %s", decision.reason)
            raw_output = await self.generate_ollama_completion(
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
            final_text = raw_output

        elif decision.circuit == DataCircuit.YELLOW:
            # ТРАНСФОРМИРУЕМЫЙ КОНТУР: маскирование -> облако -> деанонимизация
            sanitized_count = len(san_res.entity_map)
            session_id = uuid.uuid4().hex
            logger.info(
                "Маршрутизация в ТРАНСФОРМИРУЕМЫЙ контур (YELLOW) [Сессия %s, замаскировано %s сущностей]: %s",
                session_id,
                sanitized_count,
                decision.reason,
            )
            await data_sanitizer.save_vault(
                session_id, san_res.entity_map, ttl_sec=300
            )

            # Пробуем облачный инференс
            raw_output = await self.generate_cloud_completion(
                prompt=san_res.sanitized_text,
                system_prompt=request.system_prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )

            # Fallback на локальную Ollama, если облако недоступно
            if raw_output is None:
                logger.warning(
                    "LiteLLM/Gemini недоступен для YELLOW, fallback на локальную Ollama"
                )
                raw_output = await self.generate_ollama_completion(
                    prompt=san_res.sanitized_text,
                    system_prompt=request.system_prompt,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                )
                model_name = f"{self.ollama_model} (fallback)"

            # Восстанавливаем оригинальные сущности в ответе
            if raw_output is not None:
                final_text = data_sanitizer.deanonymize(
                    raw_output, san_res.entity_map
                )
            else:
                final_text = None

        else:  # GREEN
            # ОТКРЫТЫЙ КОНТУР: прямой вызов Gemini
            logger.info("Маршрутизация в ОТКРЫТЫЙ контур (GREEN): %s", decision.reason)
            raw_output = await self.generate_cloud_completion(
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )

            # Fallback на локальную Ollama
            if raw_output is None:
                logger.warning(
                    "LiteLLM/Gemini недоступен для GREEN, fallback на локальную Ollama"
                )
                raw_output = await self.generate_ollama_completion(
                    prompt=request.prompt,
                    system_prompt=request.system_prompt,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                )
                model_name = f"{self.ollama_model} (fallback)"

            final_text = raw_output

        if final_text is None:
            logger.error("Не удалось получить ответ ни от одного AI-бэкенда")
            return None

        exec_time = round((time.perf_counter() - start_time) * 1000, 2)
        response = RoutedInferenceResponse(
            text=final_text,
            circuit=decision.circuit,
            model=model_name,
            sanitized_entities_count=sanitized_count,
            execution_time_ms=exec_time,
            cached=False,
        )

        # Сохраняем в L2 кэш Redis на 1 час
        try:
            r = get_redis_client()
            await r.set(
                cache_key,
                json.dumps(response.model_dump(), ensure_ascii=False),
                ex=3600,
            )
        except Exception as e:
            logger.warning("Не удалось сохранить ответ в Redis: %s", e)

        return response


# Синглтон AI Hub для использования в сервисах
ai_hub = AIHub()
