"""
Pydantic-схемы для структурированного вывода AI Hub (Ollama / LiteLLM / Gemini).
"""
from typing import Any, List, Optional
from pydantic import BaseModel, Field


class TicketSummaryResult(BaseModel):
    """Сводка по длинной цепочке переписки инцидента."""
    core_problem: str = Field(description="Краткая суть проблемы в одном предложении")
    actions_taken: List[str] = Field(
        default_factory=list,
        description="Список уже выполненных инженерами действий и проверок",
    )
    current_status: str = Field(description="Текущее состояние инцидента на данный момент")
    recommended_next_step: str = Field(
        description="Рекомендованный следующий шаг для скорейшего решения"
    )


class ExtractedEntities(BaseModel):
    """Сущности, извлеченные из текста заявки."""
    pc_names: List[str] = Field(
        default_factory=list,
        description="Список имен компьютеров (например, NTEMW0144)",
    )
    target_users: List[str] = Field(
        default_factory=list,
        description="Список ФИО или имен сотрудников, которым требуется доступ",
    )
    printer_models: List[str] = Field(
        default_factory=list,
        description="Модели принтеров или номера кабинетов оргтехники",
    )


class AIAnalysisResult(BaseModel):
    """Глубокий анализ нетиповой или составной заявки."""
    incident_summary: str = Field(description="Краткая суть проблемы")
    primary_category: str = Field(
        description="Основная категория: 1C_ISSUE | PRINTER_ISSUE | NETWORK_WIFI | HARDWARE | PASSWORD | GENERAL"
    )
    confidence: int = Field(description="Уверенность модели от 1 до 10")
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    suggested_reply: str = Field(
        description="Вежливый лаконичный ответ заявителю по стандартам поддержки"
    )


class TaskSummaryRequest(BaseModel):
    task_id: int
    task_name: str
    task_desc: str = ""
    comments: List[dict[str, Any]] = Field(default_factory=list)
    bypass_cache: bool = False


class TaskAnalysisRequest(BaseModel):
    task_id: int
    task_name: str
    task_desc: str = ""


class AIHealthResponse(BaseModel):
    ollama_available: bool
    ollama_url: str
    ollama_model: str
    litellm_available: bool
    litellm_url: str
