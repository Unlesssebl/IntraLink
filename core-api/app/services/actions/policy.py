"""
Динамический Policy Engine для управления режимами исполнения действий.
PostgreSQL является единственным источником политик и killswitch.
"""

import logging
from app.database.db import ActionPolicyRecord, AsyncSessionLocal
from app.services.actions.registry import PolicyMode, get_action_registry

logger = logging.getLogger("core_api.services.actions.policy")

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
        Приоритет: 1. Оверрайд в PostgreSQL -> 2. default_mode в манифесте.
        """
        try:
            async with AsyncSessionLocal() as db:
                record = await db.get(ActionPolicyRecord, action_id)
                if record:
                    mode = PolicyMode(record.mode)
                    if mode == PolicyMode.AUTO and action_id not in AUTO_ELIGIBLE_ACTIONS:
                        logger.warning("Ignoring unsafe AUTO override for action '%s'", action_id)
                    else:
                        return mode
        except Exception as e:
            logger.warning("Ошибка чтения политики из PostgreSQL для %s: %s", action_id, e)
            return PolicyMode.DISABLED

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
        """Устанавливает динамический оверрайд политики действия в PostgreSQL."""
        if self.registry.get(action_id) is None:
            raise ValueError(f"Неизвестное действие '{action_id}'.")
        if mode == PolicyMode.AUTO and action_id not in AUTO_ELIGIBLE_ACTIONS:
            raise ValueError(
                f"Действие '{action_id}' не входит в allowlist безопасной автономности."
            )
        async with AsyncSessionLocal() as db:
            record = await db.get(ActionPolicyRecord, action_id)
            if record is None:
                db.add(ActionPolicyRecord(action=action_id, mode=mode.value, updated_by=actor))
            else:
                record.mode = mode.value
                record.updated_by = actor
            await db.commit()
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
        async with AsyncSessionLocal() as db:
            record = await db.get(ActionPolicyRecord, action_id)
            if record:
                await db.delete(record)
                await db.commit()

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
        action_def = self.registry.get(action_id)
        if action_def is None:
            return (
                "disabled",
                False,
                f"Неизвестное действие '{action_id}' отсутствует в Action Registry.",
            )

        admin_override = None
        try:
            async with AsyncSessionLocal() as db:
                record = await db.get(ActionPolicyRecord, action_id)
                if record:
                    admin_override = PolicyMode(record.mode)
        except Exception as e:
            logger.warning("Ошибка чтения политики из PostgreSQL для %s: %s", action_id, e)
            return ("disabled", False, "Не удалось проверить политику исполнения.")

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
        default_mode = action_def.default_mode

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
