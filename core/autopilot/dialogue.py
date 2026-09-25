"""Core Dialogue, Intent and Anti-Loop State Machine for IntraLink Autopilot.

Manages:
1. AntiLoopGuard: suppresses out-of-office / bounce mail replies and limits clarification rounds.
2. UserReplyIntentAnalyzer: classifies applicant responses in Status 6 (cancel, question, provide data, subnet mismatch).
3. Tone and urgency detection (detect_tense_tone).
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from core.intraservice.dto import ExtractedEntitiesDTO
from core.intraservice.parser import IntraServiceParser

logger = logging.getLogger("core.autopilot.dialogue")

# --- 1. Emotional / Urgency Tone Markers (Mood Tag: is_tense) ---

TENSE_PATTERNS = [
    re.compile(r"\b(?:срочно|срочнейше|срочный|горит|горят|отчет\s+горит|работа\s+стоит|встала\s+работа|не\s+могу\s+работать|критично|asap|sos)\b", re.IGNORECASE),
    re.compile(r"\b(?:сколько\s+можно\s+ждать|доколе|когда\s+уже|почему\s+так\s+долго|возмутительно|безобразие|караул|беспредел)\b", re.IGNORECASE),
    re.compile(r"\b(?:повторно\s+пишу|третий\s+раз\s+пишу|шеф\s+ругается|начальство\s+требует)\b", re.IGNORECASE),
    re.compile(r"!{2,}"),
]


def detect_tense_tone(text: str | None) -> Tuple[bool, Optional[str]]:
    """Determine whether the message indicates high urgency, distress or frustration."""
    if not text:
        return False, None
    clean_text = text.strip()
    if not clean_text:
        return False, None

    for pattern in TENSE_PATTERNS:
        match = pattern.search(clean_text)
        if match:
            matched_str = match.group(0)
            return True, f"Обнаружен маркер срочности/напряженности: '{matched_str}'"

    return False, None


# --- 2. Applicant Reply Intent Classification ---


class UserReplyIntent(str, Enum):
    PROVIDE_DATA = "provide_data"
    CANCEL_REQUEST = "cancel_request"
    CLARIFICATION_QUESTION = "clarification_question"
    SUBNET_MISMATCH = "subnet_mismatch"
    ATTACHMENTS_ONLY = "attachments_only"
    UNSUPPORTED = "unsupported"


class UserReplyIntentResult(BaseModel):
    """Result of analyzing applicant response in Status 6."""

    model_config = ConfigDict(from_attributes=True)

    intent: UserReplyIntent = Field(..., description="Classified intent of applicant reply")
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    extracted_entities: ExtractedEntitiesDTO = Field(default_factory=ExtractedEntitiesDTO)
    suggested_reply: Optional[str] = Field(default=None, description="Prompt to send back to applicant if clarification or mismatch")
    summary: str = Field(default="", description="Summary of applicant intent for internal audit note")
    invalid_ip: Optional[str] = Field(default=None, description="Non-corporate IP if detected")
    is_tense: bool = Field(default=False, description="True if emotional or urgent markers are detected")
    tense_reason: Optional[str] = Field(default=None, description="Matched keyword or reason for urgency/tension")


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

HOME_LOOPBACK_IP_REGEX = re.compile(
    r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3}|10\.0\.2\.15)\b"
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

        # Mood detection
        is_tense, tense_reason = detect_tense_tone(clean_text)

        # 1. Cancel / self-resolved request
        for pat in CANCEL_PATTERNS:
            if pat.search(clean_lower):
                return UserReplyIntentResult(
                    intent=UserReplyIntent.CANCEL_REQUEST,
                    confidence=0.98,
                    summary="Заявитель сообщил, что вопрос решен или запросил отмену заявки",
                    is_tense=is_tense,
                    tense_reason=tense_reason,
                )

        # 2. Question where to find facts
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
                    is_tense=is_tense,
                    tense_reason=tense_reason,
                )

        # 3. Home/Loopback subnet mismatch
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
                is_tense=is_tense,
                tense_reason=tense_reason,
            )

        # 4. Attachments-only response
        if has_new_attachments and len(clean_text) < 15:
            return UserReplyIntentResult(
                intent=UserReplyIntent.ATTACHMENTS_ONLY,
                confidence=0.90,
                summary="Заявитель прикрепил файл/скриншот без текстовых реквизитов (требуется осмотр инженером)",
                is_tense=is_tense,
                tense_reason=tense_reason,
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
                is_tense=is_tense,
                tense_reason=tense_reason,
            )

        # 6. Fallback unsupported
        return UserReplyIntentResult(
            intent=UserReplyIntent.UNSUPPORTED,
            confidence=0.70,
            summary="Текст ответа заявителя не содержит реквизитов оборудования",
            is_tense=is_tense,
            tense_reason=tense_reason,
        )


# --- 3. Anti-Loop Protection and Turn Limiter ---

AUTO_REPLY_PATTERNS = [
    re.compile(r"\b(?:out\s+of\s+office|auto[- ]?reply|automatic\s+reply)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:undelivered\s+mail|delivery\s+status\s+notification|failure\s+notice|mail\s+delivery\s+subsystem)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:returned\s+to\s+sender|vacation\s+responder|i\s+am\s+out\s+of\s+the\s+office|i\s+will\s+be\s+away)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:автоматический\s+ответ|автоответ|в\s+отпуске|нахожусь\s+в\s+отпуске)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:отсутствую\s+на\s+рабочем\s+месте|до\s+моего\s+возвращения|меня\s+нет\s+на\s+месте|буду\s+отсутствовать)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:сообщение\s+не\s+доставлено|недоставленное\s+сообщение|уведомление\s+о\s+недоставке|отчет\s+о\s+недоставке)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:автоматическое\s+уведомление|письмо\s+сгенерировано\s+автоматически)\b",
        re.IGNORECASE,
    ),
]


class AntiLoopDecision(BaseModel):
    """Decision outcome of anti-loop validation."""

    can_proceed: bool
    reason: str
    is_auto_reply: bool = False
    is_bot: bool = False
    rounds_count: int = 0


class AntiLoopGuard:
    """Anti-loop protection and dialogue turn limiter."""

    def __init__(self, max_clarification_rounds: int = 2) -> None:
        self.max_clarification_rounds = max_clarification_rounds

    def is_auto_reply(self, text: Optional[str], subject: Optional[str] = None) -> bool:
        combined = f"{subject or ''} {text or ''}".strip()
        if not combined:
            return False

        for pattern in AUTO_REPLY_PATTERNS:
            if pattern.search(combined):
                logger.debug("Auto-reply detected matching pattern '%s'", pattern.pattern)
                return True

        return False

    def is_bot_author(self, author_id: Optional[Union[int, str]], bot_user_id: Optional[Union[int, str]]) -> bool:
        if author_id is None or bot_user_id is None:
            return False
        try:
            return int(author_id) == int(bot_user_id)
        except (ValueError, TypeError):
            return str(author_id).strip() == str(bot_user_id).strip()

    def count_clarification_rounds(
        self,
        events_or_comments: List[Union[Dict[str, Any], Any]],
        bot_user_id: Optional[Union[int, str]] = None,
    ) -> int:
        rounds = 0
        for item in events_or_comments:
            if isinstance(item, dict):
                user_id = item.get("UserId") or item.get("EditorId") or item.get("AuthorId") or item.get("author_id")
                text = str(item.get("Comment") or item.get("Text") or item.get("text") or "")
                new_status = item.get("NewStatusId") or item.get("StatusId")
                is_private = item.get("IsPrivate") or item.get("IsPrivateComment") or False
            else:
                user_id = getattr(item, "user_id", None) or getattr(item, "author_id", None) or getattr(item, "editor_id", None)
                text = str(getattr(item, "comment", "") or getattr(item, "text", "") or "")
                new_status = getattr(item, "status_id", None) or getattr(item, "new_status_id", None)
                is_private = getattr(item, "is_private", False)

            if is_private:
                continue

            is_from_bot = self.is_bot_author(user_id, bot_user_id) if bot_user_id else False
            if is_from_bot:
                if "?" in text or new_status in (6, "6"):
                    rounds += 1
            elif new_status in (6, "6"):
                rounds += 1

        return rounds

    def check_clarification_limit(
        self,
        events_or_comments: List[Union[Dict[str, Any], Any]],
        bot_user_id: Optional[Union[int, str]] = None,
    ) -> bool:
        rounds = self.count_clarification_rounds(events_or_comments, bot_user_id=bot_user_id)
        return rounds >= self.max_clarification_rounds

    def evaluate(
        self,
        text: Optional[str] = None,
        subject: Optional[str] = None,
        author_id: Optional[Union[int, str]] = None,
        bot_user_id: Optional[Union[int, str]] = None,
        events_or_comments: Optional[List[Union[Dict[str, Any], Any]]] = None,
    ) -> AntiLoopDecision:
        if self.is_bot_author(author_id, bot_user_id):
            return AntiLoopDecision(
                can_proceed=False,
                reason="bot_self_message",
                is_bot=True,
            )

        if self.is_auto_reply(text, subject):
            return AntiLoopDecision(
                can_proceed=False,
                reason="auto_reply_detected",
                is_auto_reply=True,
            )

        rounds = 0
        if events_or_comments:
            rounds = self.count_clarification_rounds(events_or_comments, bot_user_id=bot_user_id)
            if rounds >= self.max_clarification_rounds:
                return AntiLoopDecision(
                    can_proceed=False,
                    reason=f"clarification_rounds_exceeded (count={rounds}, max={self.max_clarification_rounds})",
                    rounds_count=rounds,
                )

        return AntiLoopDecision(
            can_proceed=True,
            reason="passed",
            rounds_count=rounds,
        )
