"""
Реестр обработчиков действий (Handler Registry) для Worker SDK.
"""

from __future__ import annotations

import logging
from typing import Any
from sdk.base import ActionHandler

logger = logging.getLogger("execution_worker.sdk.registry")


class HandlerRegistry:
    """Реестр зарегистрированных обработчиков действий на узле воркера."""

    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler[Any]] = {}

    def register(self, handler: ActionHandler[Any]) -> None:
        """Регистрирует новый обработчик действия."""
        if not hasattr(handler, "id") or not handler.id:
            raise ValueError("Handler must have a non-empty 'id'")
        if handler.id in self._handlers:
            logger.warning("Перезапись обработчика для действия '%s'", handler.id)
        self._handlers[handler.id] = handler
        logger.info(
            "Зарегистрирован ActionHandler '%s' (v%s, capabilities: %s)",
            handler.id,
            handler.version,
            handler.capabilities,
        )

    def get(self, action_id: str) -> ActionHandler[Any] | None:
        """Возвращает обработчик по идентификатору действия."""
        return self._handlers.get(action_id)

    def list_capabilities(self) -> set[str]:
        """Возвращает объединение всех поддерживаемых воркером возможностей (capabilities)."""
        caps: set[str] = set()
        for h in self._handlers.values():
            caps.update(h.capabilities)
        return caps

    def list_actions(self) -> dict[str, str]:
        """Возвращает словарь поддерживаемых действий и их версий."""
        return {h.id: h.version for h in self._handlers.values()}

    def supports_action(self, action_id: str) -> bool:
        """Проверяет, поддерживается ли действие данным узлом."""
        return action_id in self._handlers

    def supports_capabilities(self, required: list[str]) -> bool:
        """Проверяет, обладает ли узел всеми запрошенными возможностями."""
        available = self.list_capabilities()
        return all(cap in available for cap in required)


_global_registry: HandlerRegistry | None = None


def get_handler_registry() -> HandlerRegistry:
    """Возвращает глобальный синглтон реестра обработчиков воркера."""
    global _global_registry
    if _global_registry is None:
        _global_registry = HandlerRegistry()
    return _global_registry


get_global_registry = get_handler_registry
