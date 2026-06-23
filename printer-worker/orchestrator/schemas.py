from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class ConnectionType(str, Enum):
    TCPIP = "tcpip"
    USB = "usb"


class ErrorType(str, Enum):
    USER = (
        "user"  # Пользователь может что-то предпринять — отправить комментарий в заявку
    )
    SYSTEM = (
        "system"  # Инфраструктурный сбой — только для логов, пользователя не тревожить
    )


class JobState(str, Enum):
    PENDING = "pending"
    ROUTING = "routing"
    PARSING = "parsing"
    PROBING = "probing"
    COPYING = "copying"
    INSTALLING = "installing"
    VERIFYING = "verifying"
    DONE = "done"
    WAITING = "waiting"
    WAITING_APPROVAL = "waiting_approval"
    FAILED = "failed"


class PrinterDriverInfo(BaseModel):
    model_key: str
    display_name: str
    driver_name: str
    driver_bundle: Optional[str] = None
    driver_inf_path: str
    vendor: str
    supported_hw_ids: List[str] = Field(default_factory=list)
    connection_type: ConnectionType


class KnowledgeBase(BaseModel):
    printer_name_prefixes: List[str] = Field(default_factory=lambda: ["ittp"])
    printers: List[PrinterDriverInfo]

    def find_by_key(self, key: str) -> Optional[PrinterDriverInfo]:
        for p in self.printers:
            if p.model_key == key:
                return p
        return None

    def find_by_name(self, name: str) -> Optional[PrinterDriverInfo]:
        """Нечёткий поиск по display_name: проверяет вхождение токенов.
        Используется когда кастомное поле IntraService содержит название
        модели, а не model_key ('Kyocera ECOSYS M2040dn KX' → kyocera_ecosys_m2040dn).
        """
        if not name:
            return None
        name_lower = name.lower()
        best: Optional[PrinterDriverInfo] = None
        best_score = 0
        for p in self.printers:
            display_lower = p.display_name.lower()
            # Подсчёт числа совпавших слов из display_name в искомом тексте
            tokens = [t for t in display_lower.split() if len(t) > 2]
            matched = sum(1 for t in tokens if t in name_lower)
            if matched > 0 and matched > best_score:
                best_score = matched
                best = p
        return best

    def find_by_hw_id(self, hw_id: str) -> Optional[PrinterDriverInfo]:
        # Поиск совпадения по Hardware ID
        hw_id_upper = hw_id.upper()
        for p in self.printers:
            for supported_id in p.supported_hw_ids:
                if (
                    supported_id.upper() in hw_id_upper
                    or hw_id_upper in supported_id.upper()
                ):
                    return p
        return None


class LLMParseResult(BaseModel):
    target_pc: str = Field(
        default="", description="NetBIOS-имя или IP-адрес целевого компьютера"
    )
    model_key: str = Field(
        default="unknown",
        description="Ключевой идентификатор модели принтера из базы знаний",
    )
    connection_type: Optional[ConnectionType] = Field(
        None, description="Тип подключения: tcpip или usb"
    )
    printer_address: Optional[str] = Field(
        None, description="IP-адрес или DNS-имя сетевого принтера (для tcpip)"
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Степень уверенности парсинга от 0.0 до 1.0",
    )

    @field_validator("connection_type", mode="before")
    def empty_str_to_none(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower in ("", "null", "none", "unknown"):
                return None
        return v

    @field_validator("target_pc", mode="before")
    def validate_target_pc(cls, v):
        if v is None:
            return ""
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower in ("null", "none"):
                return ""
        return str(v)

    @field_validator("model_key", mode="before")
    def validate_model_key(cls, v):
        if v is None or v == "":
            return "unknown"
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower in ("null", "none"):
                return "unknown"
        return str(v)

    @field_validator("printer_address", mode="before")
    def validate_printer_address(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower in ("", "null", "none"):
                return None
        return v

    @field_validator("confidence", mode="before")
    def validate_confidence(cls, v):
        if v is None:
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0


class PrintJob(BaseModel):
    task_id: int
    tg_user_id: Optional[int] = None
    raw_text: str
    state: JobState = JobState.PENDING
    target_pc: Optional[str] = None
    model_key: Optional[str] = None
    connection_type: Optional[ConnectionType] = None
    printer_address: Optional[str] = None
    driver_info: Optional[PrinterDriverInfo] = None
    error_message: Optional[str] = None
    is_manual: bool = False
    # Флаг устанавливается в probe(): True — драйвер уже есть, False — нужно копировать.
    # Хранится явно, чтобы не злоупотреблять полем error_message как каналом передачи состояния.
    driver_installed: Optional[bool] = None
    # Тип ошибки: USER — пользователь может устранить (отправляем комментарий),
    # SYSTEM — инфраструктурный сбой (тихий режим, только логи).
    error_type: ErrorType = ErrorType.SYSTEM
