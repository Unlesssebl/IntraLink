"""User reply intent analyzer for Autonomous Dialogue Loop.

Classifies applicant replies into actionable intents:
1. CANCEL_REQUEST: applicant resolved the issue or asks to cancel -> Status 30 without engineer intervention.
2. CLARIFICATION_QUESTION: applicant asks where to find network facts (IP/WKS) -> helpful illustrated prompt.
3. PROVIDE_DATA: applicant provided corporate workplace credentials -> resumes scenario execution.
4. ATTACHMENTS_ONLY: applicant uploaded sticker photo/screenshot without text -> escalates to human with notice.
5. SUBNET_MISMATCH: applicant provided home/loopback IP (192.168.x.x / 127.0.0.1) -> prompts for corporate 10.x.x.x IP.
"""

import logging
import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from core.intraservice.dto import ExtractedEntitiesDTO
from core.intraservice.parser import IntraServiceParser

logger = logging.getLogger("worker.services.intent_analyzer")


class UserReplyIntent(str, Enum):
    PROVIDE_DATA = "provide_data"
    CANCEL_REQUEST = "cancel_request"
    CLARIFICATION_QUESTION = "clarification_question"
    SUBNET_MISMATCH = "subnet_mismatch"
    ATTACHMENTS_ONLY = "attachments_only"
    UNSUPPORTED = "unsupported"


class UserReplyIntentResult(BaseModel):
    """Result of analyzing applicant response."""

    model_config = ConfigDict(from_attributes=True)

    intent: UserReplyIntent = Field(..., description="Classified intent of applicant reply")
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    extracted_entities: ExtractedEntitiesDTO = Field(default_factory=ExtractedEntitiesDTO)
    suggested_reply: Optional[str] = Field(default=None, description="Prompt to send back to applicant if clarification or mismatch")
    summary: str = Field(default="", description="Summary of applicant intent for internal audit note")
    invalid_ip: Optional[str] = Field(default=None, description="Non-corporate IP if detected")


CANCEL_PATTERNS = [
    re.compile(r"\b(?:уже\s+)?(?:не\s+)?(?:надо|нужно|требуется)\b", re.IGNORECASE),
    re.compile(r"\b(?:решил[аи]?\s+сам[аи]?|помог(?:ли)?|заработало|само\s+заработало)\b", re.IGNORECASE),
    re.compile(r"\b(?:отмените|закройте|отменяйте|не\s+актуально|отмена\s+заявки)\b", re.IGNORECASE),
    re.compile(r"\b(?:все\s+работает|уже\s+работает|вопрос\s+решен)\b", re.IGNORECASE),
]

QUESTION_PATTERNS = [
    re.compile(r"\b(?:где\s+(?:посмотреть|взять|найти|узнать))\b", re.IGNORECASE),
    re.compile(r"\b(?:как\s+(?:посмотреть|узнать|найти))\b", re.IGNORECASE),
    re.compile(r"\b(?:что\s+(?:такое|за)\s+(?:wks|ip|хост|имя\s+пк))\b", re.IGNORECASE),
    re.compile(r"\b(?:не\s+знаю\s+где|подскажите\s+где)\b", re.IGNORECASE),
]

# Non-corporate / home subnets
HOME_LOOPBACK_IP_REGEX = re.compile(
    r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3}|10\.0\.2\.15)\b"
)


class UserReplyIntentAnalyzer:
    """Deterministic, zero-latency analyzer of applicant replies in suspended tickets."""

    def __init__(self) -> None:
        self.parser = IntraServiceParser()

    def analyze_reply(
        self,
        text: Optional[str],
        has_new_attachments: bool = False,
    ) -> UserReplyIntentResult:
        """Evaluate applicant text and attachments to determine next pipeline step."""
        clean_text = (text or "").strip()
        clean_lower = clean_text.lower()

        # 1. Check for cancel / self-resolved request
        for pat in CANCEL_PATTERNS:
            if pat.search(clean_lower):
                return UserReplyIntentResult(
                    intent=UserReplyIntent.CANCEL_REQUEST,
                    confidence=0.98,
                    summary="Заявитель сообщил, что вопрос решен или запросил отмену заявки",
                )

        # 2. Check for question about where to find facts
        for pat in QUESTION_PATTERNS:
            if pat.search(clean_lower):
                return UserReplyIntentResult(
                    intent=UserReplyIntent.CLARIFICATION_QUESTION,
                    confidence=0.95,
                    suggested_reply=(
                        "Здравствуйте! "
                        "• Имя компьютера указано на белой информационной наклейке на системном блоке (начинается с WKS-XXXX). "
                        "• IP-адрес сетевого принтера указан на корпусе устройства (наклейка с IP) либо распечатывается через меню принтера (Отчет о конфигурации сети)."
                    ),
                    summary="Заявитель уточняет, где найти сетевые реквизиты оборудования",
                )

        # 3. Check for Home/Loopback subnet mismatch (e.g. 192.168.1.100)
        home_ip_match = HOME_LOOPBACK_IP_REGEX.search(clean_text)
        if home_ip_match:
            bad_ip = home_ip_match.group(0)
            return UserReplyIntentResult(
                intent=UserReplyIntent.SUBNET_MISMATCH,
                confidence=0.96,
                invalid_ip=bad_ip,
                suggested_reply=(
                    f"Здравствуйте! Указанный IP-адрес ({bad_ip}) относится к домашней/локальной сети вашего роутера. "
                    "Пожалуйста, укажите корпоративный сетевой IP-адрес принтера из диапазона 10.***.***.*** "
                    "(указан на наклейке на корпусе устройства)."
                ),
                summary=f"Заявитель указал некорпоративный домашний IP-адрес: {bad_ip}",
            )

        # 4. Check for attachments-only response (photo of sticker / screenshot)
        if has_new_attachments and len(clean_text) < 15:
            return UserReplyIntentResult(
                intent=UserReplyIntent.ATTACHMENTS_ONLY,
                confidence=0.90,
                summary="Заявитель прикрепил файл/скриншот без текстовых реквизитов (требуется осмотр инженером)",
            )

        # 5. Extract workplace facts
        extracted = self.parser.extract_entities(
            description=clean_text,
            title="",
        )

        has_facts = bool(
            extracted.pc_name
            or extracted.printer_address
            or extracted.printer_model
            or extracted.target_user
        )

        if has_facts:
            return UserReplyIntentResult(
                intent=UserReplyIntent.PROVIDE_DATA,
                confidence=0.95,
                extracted_entities=extracted,
                summary=f"Извлечены реквизиты: WKS={extracted.pc_name or '-'}, IP={extracted.printer_address or '-'}",
            )

        # 6. Fallback unsupported
        return UserReplyIntentResult(
            intent=UserReplyIntent.UNSUPPORTED,
            confidence=0.70,
            summary="Текст ответа заявителя не содержит реквизитов оборудования",
        )
