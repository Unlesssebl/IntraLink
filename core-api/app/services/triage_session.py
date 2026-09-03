"""
Менеджер сессионного состояния триажа: пропуск заявок оператором в Redis.
"""

import logging
from app.config import settings
from app.services.worker import get_redis_client

logger = logging.getLogger("core_api.services.triage_session")


class TriageSessionManager:
    """Управление сессионным списком пропущенных заявок в Redis."""

    @staticmethod
    def _get_redis():
        try:
            import app.routers.triage as tr
            return tr.get_redis_client()
        except Exception:
            return get_redis_client()

    @classmethod
    async def get_skipped_task_ids(cls, operator_id: str | None = None) -> set[int]:
        """Возвращает множество ID пропущенных заявок из Redis для указанного оператора."""
        try:
            r = cls._get_redis()
            keys_to_check = ["session:skipped_task_ids"]
            if operator_id and operator_id != "bot_or_cli":
                keys_to_check.insert(0, f"session:{operator_id}:skipped_task_ids")

            result: set[int] = set()
            for key in keys_to_check:
                raw = await r.smembers(key)
                if raw:
                    result.update({int(x) for x in raw if str(x).isdigit()})
            return result
        except Exception as e:
            logger.debug("Ошибка чтения session:skipped_task_ids из Redis: %s", e)
            return set()

    @classmethod
    async def skip_tasks(
        cls,
        task_ids: list[int],
        operator_id: str | None = None,
        ttl_seconds: int = settings.SKIPPED_TASKS_REDIS_TTL,
    ) -> int:
        """Помечает список заявок как пропущенные в текущей смене."""
        if not task_ids:
            return 0

        op = operator_id if (operator_id and operator_id != "bot_or_cli") else None
        redis_key = f"session:{op}:skipped_task_ids" if op else "session:skipped_task_ids"

        r = cls._get_redis()
        for tid in task_ids:
            if op:
                await r.sadd(redis_key, str(tid))
            await r.sadd("session:skipped_task_ids", str(tid))

        if op:
            await r.expire(redis_key, ttl_seconds)
        await r.expire("session:skipped_task_ids", ttl_seconds)
        return len(task_ids)

    @classmethod
    async def reset_session(cls, operator_id: str | None = None) -> None:
        """Сбрасывает сессионный кэш пропущенных заявок."""
        op = operator_id if (operator_id and operator_id != "bot_or_cli") else None
        r = cls._get_redis()
        await r.delete("session:skipped_task_ids")
        if op:
            await r.delete(f"session:{op}:skipped_task_ids")
