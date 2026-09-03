"""
Динамический Policy Engine для управления режимами исполнения действий (Auto / Confirm / Disabled).
Поддерживает оперативный Killswitch и хранение оверрайдов политик в Redis.
"""

import logging
from app.services.actions.registry import ActionDefinition, PolicyMode, get_action_registry
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.services.actions.policy")

POLICY_KEY_PREFIX = "policy:action:"


class PolicyEngine:
    """Движок управления политиками безопасности и режимами выполнения действий."""

    def __init__(self, registry=None):
        self.registry = registry or get_action_registry()

    async def get_action_policy(
        self, action_id: str, redis_client=None
    ) -> PolicyMode:
        """
        Возвращает действующую политику для действия.
        Приоритет: 1. Оверрайд в Redis -> 2. default_mode в манифесте -> 3. CONFIRM.
        """
        r = redis_client or get_redis_client()
        try:
            val = await r.get(f"{POLICY_KEY_PREFIX}{action_id}")
            if val:
                val_clean = str(val).lower().strip()
                if val_clean in (m.value for m in PolicyMode):
                    return PolicyMode(val_clean)
        except Exception as e:
            logger.debug("Ошибка чтения политики из Redis для %s: %s", action_id, e)

        action_def = self.registry.get(action_id)
        if action_def:
            return action_def.default_mode

        return PolicyMode.CONFIRM

    async def set_action_policy(
        self,
        action_id: str,
        mode: PolicyMode,
        actor: str = "admin",
        redis_client=None,
    ) -> None:
        """Устанавливает динамический оверрайд политики действия в Redis."""
        r = redis_client or get_redis_client()
        key = f"{POLICY_KEY_PREFIX}{action_id}"
        await r.set(key, mode.value)
        logger.info(
            "Политика действия '%s' изменена на '%s' оператором %s",
            action_id,
            mode.value,
            actor,
        )

    async def reset_action_policy(
        self, action_id: str, redis_client=None
    ) -> None:
        """Сбрасывает оверрайд политики действия к значению по умолчанию из манифеста."""
        r = redis_client or get_redis_client()
        await r.delete(f"{POLICY_KEY_PREFIX}{action_id}")

    async def evaluate_execution_mode(
        self,
        action_id: str,
        requested_mode: str | None = None,
        redis_client=None,
    ) -> tuple[str, bool, str]:
        """
        Оценивает возможность исполнения команды и итоговый режим:
        Returns:
            (effective_mode, is_allowed, reason)
        """
        r = redis_client or get_redis_client()
        admin_override = None
        try:
            val = await r.get(f"{POLICY_KEY_PREFIX}{action_id}")
            if val:
                val_clean = str(val).lower().strip()
                if val_clean in (m.value for m in PolicyMode):
                    admin_override = PolicyMode(val_clean)
        except Exception as e:
            logger.debug("Ошибка чтения политики из Redis для %s: %s", action_id, e)

        # 1. Если администратор установил Killswitch
        if admin_override == PolicyMode.DISABLED:
            return (
                "disabled",
                False,
                f"Действие '{action_id}' заблокировано администратором (Killswitch активен).",
            )

        # 2. Если администратор принудительно включил HITL
        if admin_override == PolicyMode.CONFIRM:
            return (
                "confirm",
                True,
                "Политика безопасности требует подтверждения оператора (Human-in-the-Loop).",
            )

        # 3. Если администратор разрешил AUTO
        if admin_override == PolicyMode.AUTO:
            final = requested_mode.lower().strip() if requested_mode else "auto"
            return (final, True, "Автономное выполнение разрешено администратором.")

        # 4. Нет оверрайда администратора: используем запрос клиента или default_mode
        action_def = self.registry.get(action_id)
        default_mode = action_def.default_mode.value if action_def else "confirm"

        if requested_mode and requested_mode.lower().strip() in ("auto", "confirm", "dry_run"):
            return (requested_mode.lower().strip(), True, "Выполнение разрешено.")

        return (default_mode, True, "Использован режим по умолчанию.")


_policy_engine_instance: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    """Возвращает синглтон Policy Engine."""
    global _policy_engine_instance
    if _policy_engine_instance is None:
        _policy_engine_instance = PolicyEngine()
    return _policy_engine_instance
