"""
Централизованный AI Hub для Core API (Ollama + LiteLLM / Gemini).
Включает семафор параллелизма, Redis-кэширование (Pre-Summarization) и Circuit Breaker.
"""
import asyncio
import hashlib
import json
import logging
import re
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
    RoutedInferenceRequest,
    RoutedInferenceResponse,
    TicketSummaryResult,
)
from app.services.worker import get_redis_client
from app.services.security_audit import record_security_event

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
        """
        Быстрая проверка доступности Ollama с автоматическим перебором
        кандидатов (хост-машина, docker network, localhost).
        """
        session = await self._get_session()

        # 1. Проверяем текущий сконфигурированный URL
        try:
            async with session.get(
                f"{self.ollama_url}/api/tags",
                timeout=aiohttp.ClientTimeout(total=timeout_sec),
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass

        # 2. Адаптивный fallback по альтернативным сетевым путям
        candidates = [
            settings.OLLAMA_BASE_URL.rstrip("/"),
            "http://host.docker.internal:11434",
            "http://127.0.0.1:11434",
            "http://localhost:11434",
            "http://ollama:11434",
            "http://127.0.0.1:11435",
        ]
        # Сохраняем уникальные адреса
        unique_candidates = []
        for c in candidates:
            if c and c != self.ollama_url and c not in unique_candidates:
                unique_candidates.append(c)

        for candidate in unique_candidates:
            try:
                async with session.get(
                    f"{candidate}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=timeout_sec),
                ) as resp:
                    if resp.status == 200:
                        logger.info(
                            "Ollama обнаружена на альтернативном адресе: %s (прежний: %s)",
                            candidate,
                            self.ollama_url,
                        )
                        self.ollama_url = candidate
                        return True
            except Exception:
                continue

        return False

    async def _detect_gpu_info(self) -> tuple[bool, Optional[str], Optional[str], Optional[int]]:
        """
        Детектирует использование GPU и видеопамяти через Ollama API (/api/ps)
        и системные интерфейсы (NVIDIA RTX 3050 / Vulkan / DirectML).
        """
        gpu_detected = False
        gpu_name = None
        gpu_backend = None
        vram_bytes = None

        session = await self._get_session()
        try:
            async with session.get(
                f"{self.ollama_url}/api/ps",
                timeout=aiohttp.ClientTimeout(total=1.5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = data.get("models", [])
                    if models:
                        for m in models:
                            allocated = m.get("size_vram", 0)
                            if allocated > 0:
                                gpu_detected = True
                                vram_bytes = allocated
                                break
        except Exception:
            pass

        # Если в памяти Ollama есть активная VRAM или сервис запущен
        try:
            import os
            # Проверяем видимость CUDA
            cuda_dev = os.getenv("CUDA_VISIBLE_DEVICES", "1")
            if os.path.exists(r"C:\Windows\System32\nvcuda.dll") or os.getenv("CUDA_VISIBLE_DEVICES") is not None:
                gpu_name = f"NVIDIA GeForce RTX 3050 (GPU {cuda_dev})"
                gpu_backend = "CUDA"
                gpu_detected = True
            elif os.path.exists(r"C:\Windows\System32\vulkan-1.dll") or os.path.exists("/dev/dri"):
                gpu_name = "AMD Radeon / Vulkan Compatible GPU"
                gpu_backend = "Vulkan"
                gpu_detected = True
            else:
                gpu_backend = "CPU"
        except Exception:
            gpu_backend = "CPU"

        return gpu_detected, gpu_name, gpu_backend, vram_bytes

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
        """Возвращает статус здоровья всех подключенных AI-бэкендов и GPU телеметрию."""
        ollama_ok = await self.is_ollama_available()
        litellm_ok = await self.is_litellm_available()

        gpu_detected = False
        gpu_name = None
        gpu_backend = None
        vram_bytes = None

        if ollama_ok:
            gpu_detected, gpu_name, gpu_backend, vram_bytes = await self._detect_gpu_info()

        return AIHealthResponse(
            ollama_available=ollama_ok,
            ollama_url=self.ollama_url,
            ollama_model=self.ollama_model,
            litellm_available=litellm_ok,
            litellm_url=self.litellm_url,
            gpu_detected=gpu_detected,
            gpu_name=gpu_name,
            gpu_backend=gpu_backend,
            vram_allocated_bytes=vram_bytes,
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
            except json.JSONDecodeError as jde:
                # Ollama вернула не-JSON (например, текст с извинениями или пустая строка).
                # Логируем snippet ответа для диагностики, возвращаем None.
                logger.error(
                    "Ollama вернула невалидный JSON для заявки #%s: %s | snippet: %.120r",
                    task_id, jde, content
                )
                return None
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
        response_schema: dict[str, Any] | None = None,
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
                "num_predict": max_tokens or settings.AI_OLLAMA_MAX_TOKENS,
            },
        }
        if response_schema is not None:
            payload["format"] = response_schema

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
        response_schema: dict[str, Any] | None = None,
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
        eff_max_tokens = max(max_tokens or 512, settings.AI_CLOUD_MAX_TOKENS)
        payload: dict[str, Any] = {
            "model": settings.GEMINI_MODEL,
            "messages": messages,
            "max_tokens": eff_max_tokens,
            "temperature": temperature,
        }
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_response",
                    "schema": response_schema,
                },
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
                    content = choices[0].get("message", {}).get("content", "").strip()
                    # Санитация аномального зацикливания токенов (более 3 одинаковых слов подряд)
                    content = re.sub(r"(\b\w+\b)(?:\s+\1){3,}", r"\1", content, flags=re.IGNORECASE)
                    return content
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

        # 1. Сначала определяем контур. Кэш нельзя читать до DLP-решения:
        # одинаковый prompt с force_circuit=RED не должен получить результат,
        # ранее сгенерированный облачным контуром.
        try:
            san_res = data_sanitizer.sanitize(request.prompt)
            decision = data_sanitizer.evaluate_circuit(
                prompt=request.prompt,
                metadata=request.metadata,
                sanitization_result=san_res,
            )
            if decision.circuit not in {
                DataCircuit.RED,
                DataCircuit.YELLOW,
                DataCircuit.GREEN,
            }:
                raise ValueError("unknown_data_circuit")
        except Exception as exc:
            # Fail closed: a classifier failure must never fall through to cloud.
            logger.exception("DLP routing failed; cloud inference blocked")
            await record_security_event(
                "dlp_routing",
                "blocked",
                {"reason_code": type(exc).__name__},
            )
            return None

        # 2. Проверяем L2 кэш по хэшу промпта и выбранного контура
        prompt_identity = request.prompt
        if request.prompt_variants:
            prompt_identity += ":" + "|".join(
                f"{v.profile}:{v.prompt}" for v in request.prompt_variants
            )
        cache_hash = hashlib.sha256(
            (
                f"{prompt_identity}:{request.system_prompt}:{request.temperature}:"
                f"{request.purpose.value}:{request.prompt_revision or ''}:"
                f"{json.dumps(request.response_schema, sort_keys=True, default=str)}:"
                f"{decision.circuit.value}:{decision.target_backend}:{decision.target_model}"
            ).encode()
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

        # Выбираем варианты под local / cloud
        local_var = None
        cloud_var = None
        if request.prompt_variants:
            for v in request.prompt_variants:
                if v.profile == "local":
                    local_var = v
                elif v.profile == "cloud":
                    cloud_var = v

        model_name = (
            self.ollama_model
            if decision.circuit == DataCircuit.RED
            else settings.GEMINI_MODEL
        )
        sanitized_count = 0
        raw_output: Optional[str] = None
        requested_backend = "ollama" if decision.circuit == DataCircuit.RED else "litellm_gemini"
        actual_backend = requested_backend
        fallback_used = False
        fallback_reason_code: Optional[str] = None
        context_profile: Literal["local", "cloud", "none"] = "local" if decision.circuit == DataCircuit.RED else "cloud"
        rag_refs: list[str] = []
        attempts: list[dict[str, Any]] = []

        # 3. Маршрутизация по контурам
        if decision.circuit == DataCircuit.RED:
            # ЗАКРЫТЫЙ КОНТУР: строго локальный инференс с локальным профилем
            logger.info("Маршрутизация в ЗАКРЫТЫЙ контур (RED): %s", decision.reason)
            p = local_var.prompt if local_var else request.prompt
            sp = (local_var.system_prompt if local_var else None) or request.system_prompt
            mt = local_var.max_tokens if local_var else request.max_tokens
            rag_refs = local_var.rag_refs if local_var else []
            context_profile = "local"
            actual_backend = "ollama"

            att_start = time.perf_counter()
            raw_output = await self.generate_ollama_completion(
                prompt=p,
                system_prompt=sp,
                max_tokens=mt,
                temperature=request.temperature,
                response_schema=request.response_schema,
            )
            att_dur = round((time.perf_counter() - att_start) * 1000, 2)
            attempts.append({
                "backend": "ollama",
                "profile": "local",
                "outcome": "success" if raw_output is not None else "failed",
                "reason_code": None if raw_output is not None else "local_unavailable",
                "duration_ms": att_dur,
            })
            final_text = raw_output

        elif decision.circuit == DataCircuit.YELLOW:
            # ТРАНСФОРМИРУЕМЫЙ КОНТУР: маскирование -> облако (cloud profil) -> fallback локально (local profil)
            c_prompt = cloud_var.prompt if cloud_var else request.prompt
            c_sp = (cloud_var.system_prompt if cloud_var else None) or request.system_prompt
            c_mt = cloud_var.max_tokens if cloud_var else request.max_tokens

            c_san = data_sanitizer.sanitize(c_prompt)
            sanitized_count = len(c_san.entity_map)
            session_id = uuid.uuid4().hex
            logger.info(
                "Маршрутизация в ТРАНСФОРМИРУЕМЫЙ контур (YELLOW) [Сессия %s, замаскировано %s сущностей]: %s",
                session_id,
                sanitized_count,
                decision.reason,
            )
            await data_sanitizer.save_vault(
                session_id, c_san.entity_map, ttl_sec=300
            )

            # Пробуем облачный инференс
            att_start = time.perf_counter()
            try:
                raw_output = await self.generate_cloud_completion(
                    prompt=c_san.sanitized_text,
                    system_prompt=c_sp,
                    max_tokens=c_mt,
                    temperature=request.temperature,
                    response_schema=request.response_schema,
                )
            except Exception as exc:
                logger.warning("Cloud inference failed in YELLOW circuit: %s", exc)
                raw_output = None
            att_dur = round((time.perf_counter() - att_start) * 1000, 2)

            if raw_output is not None:
                attempts.append({
                    "backend": "litellm_gemini",
                    "profile": "cloud",
                    "outcome": "success",
                    "duration_ms": att_dur,
                })
                context_profile = "cloud"
                rag_refs = cloud_var.rag_refs if cloud_var else []
                actual_backend = "litellm_gemini"
                final_text = data_sanitizer.deanonymize(raw_output, c_san.entity_map)
            else:
                attempts.append({
                    "backend": "litellm_gemini",
                    "profile": "cloud",
                    "outcome": "failed",
                    "reason_code": "cloud_unavailable",
                    "duration_ms": att_dur,
                })
                fallback_used = True
                fallback_reason_code = "cloud_unavailable"
                logger.warning(
                    "LiteLLM/Gemini недоступен для YELLOW, fallback на локальную Ollama с local-профилем"
                )

                # Fallback на локальную Ollama с local-профилем
                l_prompt = local_var.prompt if local_var else request.prompt
                l_sp = (local_var.system_prompt if local_var else None) or request.system_prompt
                l_mt = local_var.max_tokens if local_var else request.max_tokens
                l_san = data_sanitizer.sanitize(l_prompt)

                loc_start = time.perf_counter()
                raw_output = await self.generate_ollama_completion(
                    prompt=l_san.sanitized_text,
                    system_prompt=l_sp,
                    max_tokens=l_mt,
                    temperature=request.temperature,
                    response_schema=request.response_schema,
                )
                loc_dur = round((time.perf_counter() - loc_start) * 1000, 2)
                attempts.append({
                    "backend": "ollama",
                    "profile": "local",
                    "outcome": "success" if raw_output is not None else "failed",
                    "reason_code": None if raw_output is not None else "local_unavailable",
                    "duration_ms": loc_dur,
                })
                model_name = f"{self.ollama_model} (fallback)"
                context_profile = "local"
                rag_refs = local_var.rag_refs if local_var else []
                actual_backend = "ollama"
                if raw_output is not None:
                    final_text = data_sanitizer.deanonymize(raw_output, l_san.entity_map)
                else:
                    final_text = None

        else:  # GREEN
            # ОТКРЫТЫЙ КОНТУР: прямой вызов Gemini
            logger.info("Маршрутизация в ОТКРЫТЫЙ контур (GREEN): %s", decision.reason)
            c_prompt = cloud_var.prompt if cloud_var else request.prompt
            c_sp = (cloud_var.system_prompt if cloud_var else None) or request.system_prompt
            c_mt = cloud_var.max_tokens if cloud_var else request.max_tokens

            att_start = time.perf_counter()
            try:
                raw_output = await self.generate_cloud_completion(
                    prompt=c_prompt,
                    system_prompt=c_sp,
                    max_tokens=c_mt,
                    temperature=request.temperature,
                    response_schema=request.response_schema,
                )
            except Exception as exc:
                logger.warning("Cloud inference failed in GREEN circuit: %s", exc)
                raw_output = None
            att_dur = round((time.perf_counter() - att_start) * 1000, 2)

            if raw_output is not None:
                attempts.append({
                    "backend": "litellm_gemini",
                    "profile": "cloud",
                    "outcome": "success",
                    "duration_ms": att_dur,
                })
                context_profile = "cloud"
                rag_refs = cloud_var.rag_refs if cloud_var else []
                actual_backend = "litellm_gemini"
                final_text = raw_output
            else:
                attempts.append({
                    "backend": "litellm_gemini",
                    "profile": "cloud",
                    "outcome": "failed",
                    "reason_code": "cloud_unavailable",
                    "duration_ms": att_dur,
                })
                fallback_used = True
                fallback_reason_code = "cloud_unavailable"
                logger.warning(
                    "LiteLLM/Gemini недоступен для GREEN, fallback на локальную Ollama с local-профилем"
                )

                l_prompt = local_var.prompt if local_var else request.prompt
                l_sp = (local_var.system_prompt if local_var else None) or request.system_prompt
                l_mt = local_var.max_tokens if local_var else request.max_tokens

                loc_start = time.perf_counter()
                raw_output = await self.generate_ollama_completion(
                    prompt=l_prompt,
                    system_prompt=l_sp,
                    max_tokens=l_mt,
                    temperature=request.temperature,
                    response_schema=request.response_schema,
                )
                loc_dur = round((time.perf_counter() - loc_start) * 1000, 2)
                attempts.append({
                    "backend": "ollama",
                    "profile": "local",
                    "outcome": "success" if raw_output is not None else "failed",
                    "reason_code": None if raw_output is not None else "local_unavailable",
                    "duration_ms": loc_dur,
                })
                model_name = f"{self.ollama_model} (fallback)"
                context_profile = "local"
                rag_refs = local_var.rag_refs if local_var else []
                actual_backend = "ollama"
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
            actual_backend=actual_backend,
            requested_backend=requested_backend,
            model_alias=settings.GEMINI_MODEL if actual_backend == "litellm_gemini" else self.ollama_model,
            resolved_model=model_name,
            fallback_used=fallback_used,
            fallback_reason_code=fallback_reason_code,
            context_profile=context_profile,
            rag_refs=rag_refs,
            attempts=attempts,
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
