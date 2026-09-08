"""Data structures for deterministic applicant-comment classification."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel


class UserReplyIntent(str, Enum):
    """Классификация намерения ответа заявителя."""
    PROVIDE_DATA = "provide_data"                    # Заявитель предоставил IP, имя ПК или модель
    CANCEL_REQUEST = "cancel_request"                # Заявитель просит отменить/закрыть ("не актуально", "уже помогли")
    CLARIFICATION_QUESTION = "clarification_question"  # Заявитель задает встречный вопрос ("где посмотреть IP?")
    UNSUPPORTED = "unsupported"                      # Сложный текст, претензия или встречный запрос, требующий живого инженера


class IntentAnalysisResult(BaseModel):
    """Результат анализа комментария заявителя."""
    intent: UserReplyIntent = UserReplyIntent.UNSUPPORTED
    extracted_ip: Optional[str] = None
    extracted_pc: Optional[str] = None
    confidence: float = 1.0
    source: str = "regex"  # "regex" | "llm"
    summary: str = ""
    suggested_reply: Optional[str] = None
