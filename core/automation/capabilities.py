"""Typed technical capability catalog for ADR 0006."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CapabilityRisk(str, Enum):
    read_only = "read_only"
    low = "low"
    medium = "medium"
    high = "high"


class IdempotencyMode(str, Enum):
    naturally_idempotent = "naturally_idempotent"
    key_guarded = "key_guarded"
    never_auto_retry = "never_auto_retry"


class PreflightStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    degraded = "degraded"
    not_applicable = "not_applicable"


class CapabilityOutcome(str, Enum):
    succeeded = "succeeded"
    failed = "failed"
    unknown_outcome = "unknown_outcome"


class CapabilityPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PreflightStatus
    checks: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


class CapabilityExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: CapabilityOutcome
    proof: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


class CapabilityExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: int
    action_plan_id: UUID
    command_id: UUID


class CapabilityExecutor(Protocol):
    async def preflight(self, params: dict[str, Any]) -> CapabilityPreflight: ...

    async def execute(
        self, params: dict[str, Any], *, context: CapabilityExecutionContext
    ) -> CapabilityExecution: ...


class CapabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    version: str
    name: str
    description: str
    executor: str
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    risk: CapabilityRisk
    idempotency: IdempotencyMode
    is_mutating: bool
    verifies_result: bool = True
    enabled: bool = True


CapabilityHandler = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


class CapabilityRegistry:
    """Single source of truth for executable and diagnostic capabilities."""

    def __init__(self, specs: tuple[CapabilitySpec, ...] = ()) -> None:
        self._specs: dict[str, CapabilitySpec] = {}
        self._handlers: dict[str, CapabilityHandler] = {}
        self._executors: dict[str, CapabilityExecutor] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: CapabilitySpec, handler: CapabilityHandler | None = None) -> None:
        if spec.key in self._specs:
            raise ValueError(f"Duplicate capability key: {spec.key}")
        self._specs[spec.key] = spec
        if handler is not None:
            self._handlers[spec.key] = handler

    def get(self, key: str) -> CapabilitySpec | None:
        return self._specs.get(key)

    def require(self, key: str) -> CapabilitySpec:
        spec = self.get(key)
        if spec is None:
            raise KeyError(f"Unknown capability: {key}")
        return spec

    def list_all(self) -> list[CapabilitySpec]:
        return sorted(self._specs.values(), key=lambda item: item.key)

    def get_handler(self, key: str) -> CapabilityHandler | None:
        return self._handlers.get(key)

    def bind_handler(self, key: str, handler: CapabilityHandler) -> None:
        self.require(key)
        self._handlers[key] = handler

    def get_executor(self, key: str) -> CapabilityExecutor | None:
        return self._executors.get(key)

    def bind_executor(self, key: str, executor: CapabilityExecutor) -> None:
        self.require(key)
        self._executors[key] = executor

    def validate_params(self, key: str, params: dict[str, Any]) -> list[str]:
        spec = self.require(key)
        return [name for name in spec.required_params if not str(params.get(name, "")).strip()]


DEFAULT_CAPABILITIES = (
    CapabilitySpec(
        key="probe_host",
        version="1.0.0",
        name="Проверка доступности рабочего места",
        description="Read-only проверка сетевой доступности компьютера и служебных портов.",
        executor="diagnostics",
        required_params=("pc_name",),
        risk=CapabilityRisk.read_only,
        idempotency=IdempotencyMode.naturally_idempotent,
        is_mutating=False,
    ),
    CapabilitySpec(
        key="probe_printer",
        version="1.0.0",
        name="Проверка доступности принтера",
        description="Read-only проверка адреса сетевого принтера.",
        executor="diagnostics",
        required_params=("printer_address",),
        risk=CapabilityRisk.read_only,
        idempotency=IdempotencyMode.naturally_idempotent,
        is_mutating=False,
    ),
    CapabilitySpec(
        key="install_printer",
        version="1.0.0",
        name="Установка принтера",
        description="Установка зарегистрированного принтера на рабочую станцию.",
        executor="windows_exec",
        required_params=("pc_name", "connection_type"),
        optional_params=("printer_address", "printer_model", "usb_host"),
        risk=CapabilityRisk.medium,
        idempotency=IdempotencyMode.key_guarded,
        is_mutating=True,
        enabled=False,
    ),
    CapabilitySpec(
        key="reset_print_spooler",
        version="1.0.0",
        name="Сброс очереди печати",
        description="Остановка Spooler, очистка очереди, запуск и проверка службы.",
        executor="windows_exec",
        required_params=("pc_name",),
        risk=CapabilityRisk.medium,
        idempotency=IdempotencyMode.key_guarded,
        is_mutating=True,
    ),
    CapabilitySpec(
        key="set_default_printer",
        version="1.0.0",
        name="Назначение принтера по умолчанию",
        description="Назначение и повторное чтение основного принтера пользователя.",
        executor="windows_exec",
        required_params=("pc_name", "printer_address"),
        risk=CapabilityRisk.medium,
        idempotency=IdempotencyMode.key_guarded,
        is_mutating=True,
    ),
    CapabilitySpec(
        key="create_ad_user",
        version="1.0.0",
        name="Создание пользователя AD",
        description="Provisioning пользователя с защищённой записью реквизитов в заявку.",
        executor="ldap",
        required_params=("first_name", "last_name", "department", "title"),
        optional_params=("middle_name", "phone", "company"),
        risk=CapabilityRisk.high,
        idempotency=IdempotencyMode.never_auto_retry,
        is_mutating=True,
    ),
    CapabilitySpec(
        key="disable_ad_user",
        version="1.0.0",
        name="Отключение пользователя AD",
        description="Однозначный поиск, отключение и повторное чтение учётной записи.",
        executor="ldap",
        required_params=("target_user",),
        risk=CapabilityRisk.high,
        idempotency=IdempotencyMode.key_guarded,
        is_mutating=True,
    ),
    CapabilitySpec(
        key="add_wlan_group_member",
        version="1.0.0",
        name="Предоставление WLAN-доступа",
        description="Добавление пользователя в WLAN-WORKNET-ALLOW и проверка членства.",
        executor="ldap",
        required_params=("target_user",),
        risk=CapabilityRisk.medium,
        idempotency=IdempotencyMode.naturally_idempotent,
        is_mutating=True,
    ),
)


def get_default_capability_registry() -> CapabilityRegistry:
    from core.automation.executors import (
        AddWlanGroupMemberExecutor,
        HostProbeExecutor,
        InstallPrinterExecutor,
        PrinterProbeExecutor,
        ResetPrintSpoolerExecutor,
        SetDefaultPrinterExecutor,
    )
    from core.automation.executors.identity import CreateAdUserExecutor, DisableAdUserExecutor

    registry = CapabilityRegistry(DEFAULT_CAPABILITIES)
    registry.bind_executor("probe_host", HostProbeExecutor())
    registry.bind_executor("probe_printer", PrinterProbeExecutor())
    registry.bind_executor("install_printer", InstallPrinterExecutor())
    registry.bind_executor("reset_print_spooler", ResetPrintSpoolerExecutor())
    registry.bind_executor("set_default_printer", SetDefaultPrinterExecutor())
    registry.bind_executor("add_wlan_group_member", AddWlanGroupMemberExecutor())
    registry.bind_executor("create_ad_user", CreateAdUserExecutor())
    registry.bind_executor("disable_ad_user", DisableAdUserExecutor())
    return registry
