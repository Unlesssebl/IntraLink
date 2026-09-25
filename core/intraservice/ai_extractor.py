"""AI-assisted Named Entity Recognition (NER) for Helpdesk tickets via LiteLLM Gateway / Ollama."""

import asyncio
import hashlib
import json
import logging
import os
from typing import Optional

from openai import AsyncOpenAI

from core.intraservice.dto import ExtractedEntitiesDTO
from core.redis_client import get_redis_client

logger = logging.getLogger("core.intraservice.ai_extractor")

HELPDESK_NER_SYSTEM_PROMPT = """Ты — интеллектуальный модуль извлечения сущностей (NER) корпоративной службы поддержки Helpdesk / Service Desk.
Твоя задача — извлечь из темы и описания заявки следующие структурированные реквизиты:
- pc_name: имя компьютера / хост (например: WKS-1020, NTEMW1000, ARM-502).
- printer_address: IP-адрес сетевого принтера (10.x.x.x) или имя очереди (например, SCSP0001).
- printer_model: модель принтера/МФУ (например: Kyocera TASKalfa 3501i, HP LaserJet 400).
- last_name: фамилия сотрудника (например: Кузнецов).
- first_name: имя сотрудника (например: Михаил).
- middle_name: отчество сотрудника (например: Сергеевич).
- user_name: полное ФИО сотрудника (например: Кузнецов Михаил Сергеевич).
- target_user: доменный логин или аккаунт сотрудника (например: kuznetsov.m).
- title: должность сотрудника (например: Ведущий инженер, Главный бухгалтер).
- department: подразделение / отдел (например: Бухгалтерия, Отдел системного администрирования).
- company: юридическое лицо / организация (например: ООО Ромашка, АО Корпорация).
- room: кабинет / комната / локация (например: каб. 305, цех 2, АБК).
- phone: контактный телефон (например: +7 (999) 111-22-33, доб. 1234).
- tab_number: табельный номер (например: 44556).
- similar_user: сотрудник со схожими правами, по образцу которого нужно предоставить доступ (например: Иванов И.И.).

Правила:
1. Если сущность не упомянута в тексте, верни для неё пустую строку "".
2. Не придумывай данные, которых нет в тексте (без галлюцинаций).
3. Ответ верни строго в виде JSON-объекта с указанными ключами."""


class AIExtractor:
    """Extracts Helpdesk domain entities from unstructured text using LiteLLM/Ollama fast model."""

    def __init__(
        self,
        ai_client: Optional[AsyncOpenAI] = None,
        model_name: Optional[str] = None,
    ) -> None:
        base_url = os.getenv("LITELLM_BASE_URL", "http://localhost:4000/v1")
        api_key = os.getenv("LITELLM_API_KEY", "sk-intralink-dev")
        self.ai_client = ai_client or AsyncOpenAI(base_url=base_url, api_key=api_key)
        self.model_name = model_name or os.getenv("LITELLM_MODEL_FAST", "helpdesk-fast")

    async def extract_entities(
        self,
        text: str,
        timeout_sec: float = 2.5,
        use_cache: bool = True,
    ) -> ExtractedEntitiesDTO:
        """Asynchronously extract entities using fast LLM with strict latency budget and Redis caching."""
        clean_text = (text or "").strip()
        if not clean_text or len(clean_text) < 5:
            return ExtractedEntitiesDTO()

        cache_key = None
        redis_client = None
        if use_cache:
            try:
                redis_client = get_redis_client()
                text_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()[:16]
                cache_key = f"cache:ner:{self.model_name}:{text_hash}"
                cached_json = await redis_client.get(cache_key)
                if cached_json:
                    data = json.loads(cached_json)
                    return ExtractedEntitiesDTO.model_validate(data)
            except Exception as exc:
                logger.debug("Redis cache miss or error in AIExtractor: %s", exc)

        try:
            call_coro = self.ai_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": HELPDESK_NER_SYSTEM_PROMPT},
                    {"role": "user", "content": clean_text[:3000]},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            response = await asyncio.wait_for(call_coro, timeout=timeout_sec)
            content = response.choices[0].message.content or "{}"
            parsed_data = json.loads(content)

            entities = ExtractedEntitiesDTO.model_validate(parsed_data)

            if redis_client is not None and cache_key is not None:
                try:
                    await redis_client.set(cache_key, entities.model_dump_json(), ex=86400)
                except Exception:
                    pass

            return entities
        except asyncio.TimeoutError:
            logger.warning(
                "AIExtractor: timeout (%.1fs) exceeded while extracting entities for text of len %d",
                timeout_sec,
                len(clean_text),
            )
            return ExtractedEntitiesDTO()
        except Exception as exc:
            logger.warning("AIExtractor: error calling LLM (%s): %s", self.model_name, exc)
            return ExtractedEntitiesDTO()


_global_ai_extractor: Optional[AIExtractor] = None


def get_ai_extractor(ai_client: Optional[AsyncOpenAI] = None) -> AIExtractor:
    """Singleton factory for AIExtractor."""
    global _global_ai_extractor
    if _global_ai_extractor is None or ai_client is not None:
        _global_ai_extractor = AIExtractor(ai_client=ai_client)
    return _global_ai_extractor
