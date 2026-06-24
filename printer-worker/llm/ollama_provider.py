import json
import logging
import aiohttp
from .base import LLMProvider
from orchestrator.schemas import LLMParseResult
from worker_config import LLM_API_URL, LLM_MODEL_NAME
from worker_services.api_client import get_session

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    async def parse_task_text(self, text: str) -> LLMParseResult:
        prompt = (
            "Ты — аналитик Service Desk. Проанализируй текст заявки на установку принтера и извлеки параметры.\n"
            "Верни строго JSON объект, соответствующий следующей схеме:\n"
            "{\n"
            '  "target_pc": "имя компьютера (например, NTEMW0123)",\n'
            '  "model_key": "идентификатор принтера (один из: hp_lj_m428, kyocera_ecosys_m2040dn, xerox_b210)",\n'
            '  "connection_type": "tcpip или usb",\n'
            '  "printer_address": "IP-адрес или сетевое имя принтера, если применимо (иначе null)",\n'
            '  "confidence": число от 0.0 до 1.0 (степень твоей уверенности)\n'
            "}\n"
            "Если имя ПК не указано, верни пустую строку в target_pc.\n"
            "Если модель принтера не понятна, постарайся сопоставить с подходящим ключом.\n"
            f'Заявка: "{text}"\n'
            "JSON:"
        )

        url = f"{LLM_API_URL.rstrip('/')}/api/generate"
        payload = {
            "model": LLM_MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        try:
            session = await get_session()
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    response_text = data.get("response", "{}")
                    parsed_json = json.loads(response_text)
                    # Валидируем через Pydantic
                    return LLMParseResult.model_validate(parsed_json)
                else:
                    logger.error("Ошибка Ollama API [%d]", response.status)
        except Exception as e:
            logger.exception("Ошибка обращения к Ollama: %s", e)

        # Фолбэк в случае ошибок
        return LLMParseResult(
            target_pc="", model_key="unknown", connection_type="tcpip", confidence=0.0
        )
