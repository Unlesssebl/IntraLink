import json
import logging
import aiohttp
from .base import LLMProvider
from orchestrator.schemas import LLMParseResult
from worker_config import LLM_API_URL, LLM_API_KEY, LLM_MODEL_NAME
from worker_services.api_client import get_session

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    async def parse_task_text(self, text: str) -> LLMParseResult:
        url = f"{LLM_API_URL.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"

        system_instruction = (
            "You are a Service Desk assistant. Parse the user request to install a printer.\n"
            "Respond ONLY with a JSON object conforming to the schema:\n"
            "{\n"
            '  "target_pc": "computer hostname or IP address (e.g. PC-ADMIN, 192.168.1.50)",\n'
            '  "model_key": "one of: hp_lj_m428, kyocera_ecosys_m2040dn, xerox_b210",\n'
            '  "connection_type": "tcpip or usb",\n'
            '  "printer_address": "IP address or network name of the printer, if applicable (otherwise null)",\n'
            '  "confidence": value between 0.0 and 1.0 (float)\n'
            "}"
        )

        payload = {
            "model": LLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }

        try:
            session = await get_session()
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    response_text = data["choices"][0]["message"]["content"]
                    parsed_json = json.loads(response_text)
                    return LLMParseResult.model_validate(parsed_json)
                else:
                    logger.error("Ошибка OpenAI API [%d]", response.status)
        except Exception as e:
            logger.exception("Ошибка обращения к OpenAI-совместимому API: %s", e)

        # Фолбэк в случае ошибок
        return LLMParseResult(
            target_pc="", model_key="unknown", connection_type="tcpip", confidence=0.0
        )
