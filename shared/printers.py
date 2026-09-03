"""
SSOT Справочник и схемы базы знаний принтеров (Knowledge Base).
"""

import json
import logging
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, Field

logger = logging.getLogger("shared.printers")

KB_FILE_PATH = Path(__file__).resolve().parent / "printers_knowledge_base.json"


class PrinterConfig(BaseModel):
    model_key: str = Field(..., description="Уникальный идентификатор модели/драйвера")
    display_name: str = Field(..., description="Человекочитаемое имя принтера")
    driver_name: str = Field(..., description="Точное имя драйвера в хранилище Windows")
    driver_inf_path: str = Field(..., description="Сетевой UNC-путь к .inf файлу драйвера")
    vendor: str = Field(..., description="Производитель (hp, kyocera, xerox, etc.)")
    driver_bundle: str | None = Field(None, description="Имя пакета драйвера при наличии")
    supported_hw_ids: list[str] = Field(default_factory=list, description="Список USB HWID")
    connection_type: Literal["tcpip", "usb"] = Field("tcpip", description="Тип подключения")


class PrintersKnowledgeBase(BaseModel):
    printer_name_prefixes: list[str] = Field(
        default_factory=list, description="Префиксы сетевых очередей (ittp, kzmp, kmkp)"
    )
    printers: list[PrinterConfig] = Field(
        default_factory=list, description="Список зарегистрированных профилей принтеров"
    )


_KB_CACHE: PrintersKnowledgeBase | None = None


def load_printers_kb(force_reload: bool = False) -> PrintersKnowledgeBase:
    """Загружает базу знаний принтеров из JSON файла (SSOT)."""
    global _KB_CACHE
    if _KB_CACHE is not None and not force_reload:
        return _KB_CACHE

    if not KB_FILE_PATH.exists():
        logger.warning("Файл базы знаний принтеров не найден: %s", KB_FILE_PATH)
        _KB_CACHE = PrintersKnowledgeBase()
        return _KB_CACHE

    try:
        data = json.loads(KB_FILE_PATH.read_text(encoding="utf-8"))
        _KB_CACHE = PrintersKnowledgeBase.model_validate(data)
        return _KB_CACHE
    except Exception as e:
        logger.error("Ошибка загрузки базы знаний принтеров: %s", e)
        _KB_CACHE = PrintersKnowledgeBase()
        return _KB_CACHE


def find_printer_by_name(printer_name: str) -> PrinterConfig | None:
    """
    Интеллектуальный поиск профиля принтера по сетевому имени или модели.
    """
    kb = load_printers_kb()
    p_lower = printer_name.lower().strip()

    # 1. По точному model_key
    for p in kb.printers:
        if p.model_key.lower() == p_lower:
            return p

    # 2. По вхождению в display_name или driver_name
    for p in kb.printers:
        if p_lower in p.display_name.lower() or p_lower in p.driver_name.lower():
            return p

    # 3. По префиксу вендора в названии принтера
    if "hp" in p_lower:
        for p in kb.printers:
            if p.vendor == "hp":
                return p
    elif "kyo" in p_lower or "km" in p_lower:
        for p in kb.printers:
            if p.vendor == "kyocera":
                return p
    elif "xerox" in p_lower or "b210" in p_lower:
        for p in kb.printers:
            if p.vendor == "xerox":
                return p

    # 4. Fallback на HP UPD по умолчанию
    for p in kb.printers:
        if p.model_key == "hp_universal_upd":
            return p

    return kb.printers[0] if kb.printers else None
