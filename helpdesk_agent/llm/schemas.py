"""
Pydantic-схемы для структурированного вывода локальной нейросети Ollama.
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class TicketSummaryResult(BaseModel):
    """Сводка по длинной цепочке переписки инцидента."""
    core_problem: str = Field(description="Краткая суть проблемы в одном предложении")
    actions_taken: List[str] = Field(description="Список уже выполненных инженерами действий и проверок")
    current_status: str = Field(description="Текущее состояние инцидента на данный момент")
    recommended_next_step: str = Field(description="Рекомендованный следующий шаг для скорейшего решения")


class ExtractedEntities(BaseModel):
    pc_names: List[str] = Field(default_factory=list, description="Список имен компьютеров (например, NTEMW0144)")
    target_users: List[str] = Field(default_factory=list, description="Список ФИО или имен сотрудников, которым требуется доступ")
    printer_models: List[str] = Field(default_factory=list, description="Модели принтеров или номера кабинетов оргтехники")


class AIAnalysisResult(BaseModel):
    """Глубокий анализ нетиповой заявки."""
    incident_summary: str = Field(description="Краткая суть проблемы")
    primary_category: str = Field(description="Основная категория: 1C_ISSUE | PRINTER_ISSUE | NETWORK_WIFI | HARDWARE | PASSWORD | GENERAL")
    confidence: int = Field(description="Уверенность модели от 1 до 10")
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    suggested_reply: str = Field(description="Вежливый лаконичный ответ заявителю по стандартам поддержки")
