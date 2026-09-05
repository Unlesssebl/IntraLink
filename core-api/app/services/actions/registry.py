"""
Реестр манифестов действий (Action Registry) для модульного монорепозитория IntraLink.
Определяет формальные контракты, параметры и политики безопасности для исполняемых действий.
"""

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class PolicyMode(str, Enum):
    AUTO = "auto"          # Автономное выполнение
    CONFIRM = "confirm"    # Human-in-the-Loop (требует подтверждения)
    DISABLED = "disabled"  # Аварийный Killswitch (действие заблокировано)


class ActionDefinition(BaseModel):
    id: str = Field(..., description="Уникальный системный идентификатор действия")
    name: str = Field(..., description="Человекочитаемое название действия")
    category: str = Field(..., description="Категория (hardware, network, access, triage, ai)")
    description: str = Field(..., description="Подробное описание назначения действия")
    default_mode: PolicyMode = Field(
        PolicyMode.CONFIRM, description="Политика исполнения по умолчанию"
    )
    target_type: str = Field(..., description="Тип целевого объекта: host | user | ticket | system")
    executor: str = Field(
        ...,
        description="Исполнитель действия: windows | backend",
    )
    risk_level: int = Field(1, ge=0, le=3, description="Уровень риска R0-R3")
    implemented: bool = Field(
        True,
        description="Есть ли в выбранном исполнителе проверенная реализация действия",
    )
    parameters_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON-схема валидации входных параметров"
    )


class ActionRegistry:
    """Реестр зарегистрированных действий системы."""

    def __init__(self):
        self._actions: dict[str, ActionDefinition] = {}
        self._register_default_actions()

    def register(self, action: ActionDefinition) -> None:
        self._actions[action.id] = action

    def get(self, action_id: str) -> ActionDefinition | None:
        return self._actions.get(action_id)

    def list_all(self) -> list[ActionDefinition]:
        return list(self._actions.values())

    def _register_default_actions(self) -> None:
        # 1. Установка принтера
        self.register(
            ActionDefinition(
                id="install_printer",
                name="Установка принтера",
                category="hardware",
                description="Удаленная установка сетевого или USB-принтера через WinRM/WMI.",
                default_mode=PolicyMode.CONFIRM,
                target_type="host",
                executor="windows",
                risk_level=1,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "printer_name": {"type": "string", "description": "Имя или модель принтера"},
                        "printer_ip": {"type": "string", "description": "IP-адрес принтера"},
                        "pc_name": {"type": "string", "description": "Целевая рабочая станция"},
                    },
                    "required": ["pc_name", "printer_name"],
                },
            )
        )

        # 2. Доступ к корпоративному Wi-Fi (WLAN)
        self.register(
            ActionDefinition(
                id="grant_wlan",
                name="Предоставление доступа к Wi-Fi",
                category="access",
                description="Добавление доменной учетной записи в группу безопасности WLAN-WORKNET.",
                default_mode=PolicyMode.CONFIRM,
                target_type="user",
                executor="windows",
                risk_level=2,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "identity": {"type": "string", "description": "sAMAccountName или UPN пользователя"},
                    },
                    "required": ["identity"],
                },
            )
        )

        # 3. Экспресс-диагностика хоста
        self.register(
            ActionDefinition(
                id="diagnose_host",
                name="Экспресс-диагностика рабочей станции",
                category="network",
                description="Проверка доступности ПК (Ping, DNS, порты SMB:445, WinRM:5985, WMI:135).",
                default_mode=PolicyMode.AUTO,
                target_type="host",
                executor="windows",
                risk_level=0,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "host": {"type": "string", "description": "Имя хоста или IP-адрес"},
                    },
                    "required": ["host"],
                },
            )
        )

        # 4. Создание учетной записи AD
        self.register(
            ActionDefinition(
                id="create_user",
                name="Создание пользователя Active Directory",
                category="access",
                description="Автоматизированное создание учетной записи сотрудника в домене.",
                default_mode=PolicyMode.CONFIRM,
                target_type="user",
                executor="windows",
                risk_level=2,
                implemented=False,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "username": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "department": {"type": "string"},
                    },
                    "required": ["username", "first_name", "last_name"],
                },
            )
        )

        # 5. Сброс пароля AD
        self.register(
            ActionDefinition(
                id="reset_password",
                name="Сброс пароля пользователя Active Directory",
                category="access",
                description="Генерация временного пароля и установка флага обязательной смены.",
                default_mode=PolicyMode.CONFIRM,
                target_type="user",
                executor="windows",
                risk_level=2,
                implemented=False,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "username": {"type": "string"},
                    },
                    "required": ["username"],
                },
            )
        )

        # 6. Пакетное применение триажа
        self.register(
            ActionDefinition(
                id="apply_triage",
                name="Применение решения триажа",
                category="triage",
                description="Атомарный перевод заявки в целевой статус со списанием трудозатрат.",
                # Изменяет статус и комментарий в заявке: только через HITL.
                default_mode=PolicyMode.CONFIRM,
                target_type="ticket",
                executor="backend",
                risk_level=1,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "task_ids": {"type": "array", "items": {"type": "integer"}},
                        "status_id": {"type": "integer"},
                        "comment": {"type": "string"},
                        "expenses": {"type": "integer"},
                    },
                    "required": ["task_ids", "status_id"],
                },
            )
        )

        # 7. Синхронизация базы знаний RAG
        self.register(
            ActionDefinition(
                id="rag_sync",
                name="Синхронизация базы знаний Helpdesk",
                category="ai",
                description="Выгрузка закрытых заявок из IntraService и индексация в pgvector.",
                default_mode=PolicyMode.AUTO,
                target_type="system",
                executor="backend",
                risk_level=0,
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "days": {"type": "integer", "default": 30},
                        "limit": {"type": "integer", "default": 50},
                    },
                },
            )
        )


_registry_instance: ActionRegistry | None = None


def get_action_registry() -> ActionRegistry:
    """Возвращает синглтон реестра действий."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ActionRegistry()
    return _registry_instance
