"""
Динамический Policy Engine для управления режимами исполнения действий (Auto / Confirm / Disabled).
Поддерживает оперативный Killswitch и хранение оверрайдов политик в Redis.
"""

import logging
from app.services.actions.registry import ActionDefinition, PolicyMode, get_action_registry
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.services.actions.policy")

POLICY_KEY_PREFIX = "policy:action:"

# Автономность — исключение, а не удобная настройка вызывающего клиента.
# Этот набор одновременно является технической границей между безопасными
# read-only задачами и действиями, меняющими заявки/доступы/инфраструктуру.
AUTO_ELIGIBLE_ACTIONS = frozenset({"diagnose_host", "rag_sync"})


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
                    mode = PolicyMode(val_clean)
                    if mode == PolicyMode.AUTO and action_id not in AUTO_ELIGIBLE_ACTIONS:
                        logger.warning("Ignoring unsafe AUTO override for action '%s'", action_id)
                    else:
                        return mode
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
        if mode == PolicyMode.AUTO and action_id not in AUTO_ELIGIBLE_ACTIONS:
            raise ValueError(
                f"Действие '{action_id}' не входит в allowlist безопасной автономности."
            )
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

        # 3. Нормализуем запрос. dry_run не меняет внешнее состояние и разрешён
        # для любого зарегистрированного действия.
        requested = (requested_mode or "").lower().strip()
        if requested == "dry_run":
            return ("dry_run", True, "Разрешена безопасная симуляция без изменений.")

        # 4. Нет оверрайда администратора: используем статическую границу
        # реестра. Запрос клиента не может повысить CONFIRM до AUTO.
        action_def = self.registry.get(action_id)
        default_mode = action_def.default_mode if action_def else PolicyMode.CONFIRM

        if requested == "confirm":
            return ("confirm", True, "Запрошено подтверждение оператора.")

        if admin_override == PolicyMode.AUTO and action_id in AUTO_ELIGIBLE_ACTIONS:
            return ("auto", True, "Автономное выполнение разрешено администратором.")

        if requested == "auto" and default_mode != PolicyMode.AUTO:
            return (
                "confirm",
                True,
                "Действие требует подтверждения оператора; запрос AUTO понижен до CONFIRM.",
            )

        return (default_mode.value, True, "Использован режим по умолчанию.")


_policy_engine_instance: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    """Возвращает синглтон Policy Engine."""
    global _policy_engine_instance
    if _policy_engine_instance is None:
        _policy_engine_instance = PolicyEngine()
    return _policy_engine_instance
