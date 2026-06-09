from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

class ConnectionType(str, Enum):
    TCPIP = "tcpip"
    USB = "usb"

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
    driver_inf_path: str
    vendor: str
    supported_hw_ids: List[str] = Field(default_factory=list)
    connection_type: ConnectionType

class KnowledgeBase(BaseModel):
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
                if supported_id.upper() in hw_id_upper or hw_id_upper in supported_id.upper():
                    return p
        return None

class LLMParseResult(BaseModel):
    target_pc: str = Field(..., description="NetBIOS-имя или IP-адрес целевого компьютера")
    model_key: str = Field(..., description="Ключевой идентификатор модели принтера из базы знаний")
    connection_type: Optional[ConnectionType] = Field(None, description="Тип подключения: tcpip или usb")
    printer_address: Optional[str] = Field(None, description="IP-адрес или DNS-имя сетевого принтера (для tcpip)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Степень уверенности парсинга от 0.0 до 1.0")

    @field_validator("connection_type", mode="before")
    def empty_str_to_none(cls, v):
        if v == "":
            return None
        return v

class PrintJob(BaseModel):
    task_id: int
    tg_user_id: int
    raw_text: str
    state: JobState = JobState.PENDING
    target_pc: Optional[str] = None
    model_key: Optional[str] = None
    connection_type: Optional[ConnectionType] = None
    printer_address: Optional[str] = None
    driver_info: Optional[PrinterDriverInfo] = None
    error_message: Optional[str] = None

