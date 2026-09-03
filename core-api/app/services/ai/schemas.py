"""
Pydantic-схемы для структурированного вывода AI Hub (Ollama / LiteLLM / Gemini)
и многоконтурного роутинга данных (Red/Yellow/Green).
"""
from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field


class DataCircuit(str, Enum):
    """Контуры чувствительности данных."""
    RED = "red"          # Закрытый контур: Строго локальный инференс (Ollama / Local vLLM)
    YELLOW = "yellow"    # Трансформируемый контур: Десенсибилизация (PII Vault) -> Cloud Gemini -> Деанонимизация
    GREEN = "green"      # Открытый контур: Прямой запрос к Cloud Gemini без маскирования


class EntityType(str, Enum):
    """Категории извлекаемых и маскируемых сущностей."""
    IP_ADDRESS = "ip"
    HOSTNAME = "host"
    USER_NAME = "user"
    EMAIL = "email"
    PHONE = "phone"
    CREDENTIAL = "credential"
    CUSTOM = "custom"


class RoutingMetadata(BaseModel):
    """Метаданные контекста для определения контура безопасности."""
    contains_credentials: bool = Field(
        default=False,
        description="Флаг наличия учетных данных/паролей в контексте (автоматически RED)",
    )
    is_confidential: bool = Field(
        default=False,
        description="Флаг конфиденциальной заявки (СБ, кадры, финансы)",
    )
    service_id: Optional[int] = Field(
        default=None,
        description="ID раздела IntraService",
    )
    force_circuit: Optional[DataCircuit] = Field(
        default=None,
        description="Принудительный выбор контура в обход автоматического классификатора",
    )


class SanitizationResult(BaseModel):
    """Результат десенсибилизации текста."""
    sanitized_text: str = Field(description="Обезличенный текст с подставленными плейсхолдерами")
    entity_map: dict[str, str] = Field(
        default_factory=dict,
        description="Таблица обратного соответствия: { '{{USER_1}}': 'Иванов Иван' }",
    )
    detected_types: List[str] = Field(
        default_factory=list,
        description="Список обнаруженных типов сущностей",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Идентификатор сессии в PII Vault (Redis) для отложенной деанонимизации",
    )


class RouteDecision(BaseModel):
    """Решение маршрутизатора о выборе контура и модели."""
    circuit: DataCircuit = Field(description="Выбранный контур данных")
    reason: str = Field(description="Обоснование выбора контура")
    target_backend: str = Field(description="Целевой бэкенд: ollama | litellm_gemini")
    target_model: str = Field(description="Название используемой модели")
    requires_sanitization: bool = Field(
        default=False,
        description="Требуется ли предварительное маскирование PII",
    )


class RoutedInferenceRequest(BaseModel):
    """Запрос на генерацию с автоматической маршрутизацией по контурам."""
    prompt: str = Field(description="Основной текст запроса / промпт")
    system_prompt: Optional[str] = Field(
        default=None,
        description="Системная инструкция для LLM",
    )
    metadata: RoutingMetadata = Field(
        default_factory=RoutingMetadata,
        description="Метаданные безопасности для классификации",
    )
    max_tokens: int = Field(default=512, description="Максимум генерируемых токенов")
    temperature: float = Field(default=0.0, description="Температура генерации")
    bypass_cache: bool = False


class RoutedInferenceResponse(BaseModel):
    """Ответ генерации с информацией о контуре и примененной маскировке."""
    text: str = Field(description="Итоговый деанонимизированный ответ LLM")
    circuit: DataCircuit = Field(description="Использованный контур данных")
    model: str = Field(description="Модель, выполнившая инференс")
    sanitized_entities_count: int = Field(
        default=0,
        description="Количество замаскированных и восстановленных PII сущностей",
    )
    execution_time_ms: float = Field(
        default=0.0,
        description="Время выполнения запроса в миллисекундах",
    )
    cached: bool = Field(default=False, description="Был ли ответ получен из L2 кэша")


class SanitizePreviewRequest(BaseModel):
    """Запрос на предварительный просмотр маскирования и маршрутизации."""
    text: str = Field(description="Текст для анализа")
    metadata: RoutingMetadata = Field(default_factory=RoutingMetadata)


class SanitizePreviewResponse(BaseModel):
    """Результат предварительного анализа текста и выбора контура."""
    original_text: str
    sanitized_text: str
    entity_map: dict[str, str]
    detected_types: List[str]
    route_decision: RouteDecision


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
    gpu_detected: bool = False
    gpu_name: Optional[str] = None
    gpu_backend: Optional[str] = None
    vram_allocated_bytes: Optional[int] = None
