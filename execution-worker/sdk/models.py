"""
Модели данных и структуры контекста Worker SDK.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class RiskClass(str, Enum):
    """Класс риска и допустимости автоматического перезапуска действия."""
    SAFE_RETRY = "safe_retry"                      # Безопасные read-only действия (повтор без side effect)
    RECONCILE_BEFORE_RETRY = "reconcile_before_retry"  # Условно идемпотентные действия (повтор только после reconcile)
    NEVER_AUTO_RETRY = "never_auto_retry"          # Изменяющие действия (любой сбой -> needs_review)


class ExecutionPhase(str, Enum):
    """Фазы жизненного цикла исполнения действия."""
    VALIDATE = "validate"
    PREFLIGHT = "preflight"
    PREPARE = "prepare"
    EXECUTE = "execute"
    VERIFY = "verify"
    RECONCILE = "reconcile"
    CLEANUP = "cleanup"


@dataclass
class HandlerContext:
    """Контекст исполнения действия на узле воркера."""
    command_id: str
    task_id: int = 0
    node_id: str = "default_node"
    worker_id: str = "default_worker"
    target_node: str | None = None
    node_name: str | None = None
    claim_token: str | None = None
    redis: Any = None
    redis_client: Any = None
    api_client: Any = None
    core_api_client: Any = None
    cancellation_token: asyncio.Event = field(default_factory=asyncio.Event)
    log: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.redis_client is not None and self.redis is None:
            self.redis = self.redis_client
        if self.core_api_client is not None and self.api_client is None:
            self.api_client = self.core_api_client
        if self.api_client is not None and self.core_api_client is None:
            self.core_api_client = self.api_client
        if self.node_name is not None and (self.node_id == "default_node" or not self.node_id):
            self.node_id = self.node_name


class ActionResult(BaseModel):
    """Типизированный результат выполнения действия воркером."""
    success: bool
    message: str
    error: str | None = None
    log: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    failure_kind: str | None = None
    failure_code: str | None = None
    verified_failure: bool = False
