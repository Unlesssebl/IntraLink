"""
Анализатор намерений заявителя (Intent Analyzer).
Двухуровневый контур:
  Tier 1: Мгновенный детерминированный Regex (IP-адрес, имя ПК, ключевые фразы отмены)
  Tier 2: Семантическая классификация через локальную LLM Qwen 2.5 (RED-контур)
"""

import asyncio
import logging
import re
from typing import Any, Optional

from app.services.lifecycle.models import IntentAnalysisResult, UserReplyIntent

logger = logging.getLogger("core_api.services.lifecycle.intent_analyzer")

# Регулярные выражения для Tier 1
IP_REGEX = re.compile(
    r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
PC_REGEX = re.compile(r"\b(NTEMW\d{4}|PC-[A-Za-z0-9_-]+|DESKTOP-[A-Za-z0-9_-]+|WS-[A-Za-z0-9_-]+)\b", re.IGNORECASE)

CANCEL_PATTERNS = [
    r"не\s+актуальн",
    r"уже\s+не\s+надо",
    r"уже\s+не\s+нужно",
    r"уже\s+помогл",
    r"уже\s+сделал",
    r"решили\s+сами",
    r"само\s+заработа",
    r"закройте\b",
    r"отмените\b",
    r"прошу\s+закрыть",
    r"прошу\s+отменить",
    r"можно\s+закрывать",
]

QUESTION_PATTERNS = [
    r"где\s+(?:посмотреть|найти|написан|узнать)",
    r"как\s+(?:посмотреть|найти|узнать|определить)",
    r"не\s+знаю\s+(?:где|какой|как)",
    r"подскажите\s+где",
]


class IntentAnalyzer:
    """Двухуровневый классификатор ответов заявителей в цикле уточнения."""

    @classmethod
    def analyze_fast_regex(cls, text: str) -> Optional[IntentAnalysisResult]:
        """
        Tier 1: Быстрый детерминированный анализ по регулярным выражениям.
        """
        clean_text = (text or "").strip()
        if not clean_text:
            return None

        clean_lower = clean_text.lower()

        # 1. Проверка явного отказа/отмены
        for pat in CANCEL_PATTERNS:
            if re.search(pat, clean_lower):
                return IntentAnalysisResult(
                    intent=UserReplyIntent.CANCEL_REQUEST,
                    confidence=0.98,
                    source="regex",
                    summary="Заявитель запросил закрытие/отмену заявки",
                )

        # 2. Проверка извлечения IP и ПК
        ip_match = IP_REGEX.search(clean_text)
        pc_match = PC_REGEX.search(clean_text)

        if ip_match or pc_match:
            extracted_ip = ip_match.group(1) if ip_match else None
            extracted_pc = pc_match.group(1).upper() if pc_match else None
            return IntentAnalysisResult(
                intent=UserReplyIntent.PROVIDE_DATA,
                extracted_ip=extracted_ip,
                extracted_pc=extracted_pc,
                confidence=0.95,
                source="regex",
                summary=f"Извлечены реквизиты: IP={extracted_ip}, PC={extracted_pc}",
            )

        # 3. Проверка встречного вопроса о реквизитах
        for pat in QUESTION_PATTERNS:
            if re.search(pat, clean_lower):
                return IntentAnalysisResult(
                    intent=UserReplyIntent.CLARIFICATION_QUESTION,
                    confidence=0.90,
                    source="regex",
                    summary="Заявитель уточняет, где найти сетевые реквизиты",
                    suggested_reply="IP-адрес сетевого принтера можно посмотреть на информационной наклейке на корпусе устройства либо в меню принтера (раздел 'Отчет о конфигурации / Сеть').",
                )

        return None

    @classmethod
    async def analyze_with_llm(
        cls, text: str, task_context: Optional[dict[str, Any]] = None
    ) -> IntentAnalysisResult:
        """
        Tier 2: Семантический анализ через локальную LLM Qwen 2.5 (RED-контур).
        Вызывается только если Tier 1 не дал однозначного совпадения.
        """
        clean_text = (text or "").strip()
        if not clean_text:
            return IntentAnalysisResult(
                intent=UserReplyIntent.UNSUPPORTED,
                confidence=1.0,
                source="empty",
            )

        # Сначала пробуем Tier 1
        fast_res = cls.analyze_fast_regex(clean_text)
        if fast_res:
            return fast_res

        # Если быстрый regex не дал совпадений, обращаемся к LLM
        prompt = (
            "Ты ассистент службы технической поддержки Helpdesk.\n"
            "Заявитель оставил комментарий в заявке на обслуживание оборудования (принтер / рабочее место).\n"
            "Определи намерение (intent) заявителя:\n"
            "- provide_data: заявитель сообщает сетевой адрес, имя компьютера, номер кабинета или модель техники.\n"
            "- cancel_request: заявитель просит отменить или закрыть заявку (решил сам, помогли, не актуально).\n"
            "- clarification_question: заявитель задает вопрос, где найти нужные данные или как их посмотреть.\n"
            "- unsupported: жалоба, сложный встречный вопрос или нерелевантный текст, требующий живого инженера.\n\n"
            f"Текст комментария заявителя:\n\"{clean_text[:500]}\"\n\n"
            "Ответь ИСКЛЮЧИТЕЛЬНО в формате JSON:\n"
            "{\n"
            '  "intent": "provide_data | cancel_request | clarification_question | unsupported",\n'
            '  "extracted_ip": "IP-адрес если упомянут, иначе null",\n'
            '  "extracted_pc": "Имя ПК если упомянуто, иначе null",\n'
            '  "summary": "Краткая суть ответа заявителя на русском",\n'
            '  "confidence": 0.9\n'
            "}"
        )

        try:
            from app.services.ai.hub import ai_hub
            from app.services.ai.schemas import DataCircuit, RoutedInferenceRequest, RoutingMetadata

            req = RoutedInferenceRequest(
                prompt=prompt,
                system_prompt="Отвечай строго валидным JSON без markdown разметки.",
                metadata=RoutingMetadata(force_circuit=DataCircuit.RED),
                max_tokens=150,
                temperature=0.0,
            )
            res = await asyncio.wait_for(ai_hub.dispatch_routed_inference(req), timeout=6.0)
            raw_text = (getattr(res, "text", "") or getattr(res, "final_text", "")).strip()

            if "" in raw_text:
                raw_text = re.sub(r"^(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
                raw_text = re.sub(r"\s*$", "", raw_text, flags=re.MULTILINE).strip()

            json_match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
            if json_match:
                import json
                data = json.loads(json_match.group(0))
                intent_raw = str(data.get("intent", "unsupported")).lower().strip()
                
                intent_map = {
                    "provide_data": UserReplyIntent.PROVIDE_DATA,
                    "cancel_request": UserReplyIntent.CANCEL_REQUEST,
                    "clarification_question": UserReplyIntent.CLARIFICATION_QUESTION,
                }
                final_intent = intent_map.get(intent_raw, UserReplyIntent.UNSUPPORTED)

                return IntentAnalysisResult(
                    intent=final_intent,
                    extracted_ip=data.get("extracted_ip"),
                    extracted_pc=data.get("extracted_pc"),
                    confidence=float(data.get("confidence", 0.8)),
                    source="llm",
                    summary=str(data.get("summary", "")),
                )
        except Exception as exc:
            logger.warning("Tier 2 LLM Intent analysis error for text '%s': %s", clean_text[:60], exc)

        return IntentAnalysisResult(
            intent=UserReplyIntent.UNSUPPORTED,
            confidence=0.5,
            source="fallback",
            summary="Не удалось автоматически разобрать намерение заявителя",
        )
