"""
Модели данных и структуры конечного автомата жизненного цикла заявок (Lifecycle FSM).
"""

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class TicketLifecycleState(str, Enum):
    """Канонические состояния тикета в рамках жизненного цикла."""
    OPEN = "open"                                  # Статус 31
    AWAITING_CLARIFICATION = "awaiting_clarification"  # Статус 35
    IN_PROGRESS = "in_progress"                    # Статус 27
    COMPLETED = "completed"                        # Статус 29
    CANCELLED = "cancelled"                        # Статус 30
    CLOSED = "closed"                              # Статус 28
    UNKNOWN = "unknown"

    @classmethod
    def from_status_id(cls, status_id: int | None) -> "TicketLifecycleState":
        mapping = {
            31: cls.OPEN,
            35: cls.AWAITING_CLARIFICATION,
            27: cls.IN_PROGRESS,
            29: cls.COMPLETED,
            30: cls.CANCELLED,
            28: cls.CLOSED,
        }
        return mapping.get(status_id or 0, cls.UNKNOWN)


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


class LifecycleStepResult(BaseModel):
    """Результат выполнения шага автономии по заявке."""
    task_id: int
    action_taken: str
    previous_status_id: Optional[int] = None
    target_status_id: Optional[int] = None
    comment: Optional[str] = None
    expenses: int = 0
    success: bool = True
    error: Optional[str] = None
    escalated_to_human: bool = False
    dispatched_command_id: Optional[str] = None
