"""
Модуль защитных механизмов (Safety Mechanisms):
1. Distributed Host Concurrency Lock (lock:host:<pc_name>) для предотвращения
   параллельных WinRM/WMI сессий к одной рабочей станции (защита от WinRM ошибки 0x80338029).
2. Dead Man's Switch / Rate Limiter для защиты от лавины случайных массовых закрытий
   заявок в очереди триажа (скользящее окно ratelimit:triage:apply).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging
import time
from typing import Any
import uuid

from app.config import settings
from app.services.worker import get_redis_client
from app.utils.normalizer import normalize_pc_name

logger = logging.getLogger("core_api.services.safety")

# Lua-скрипт для безопасного атомарного освобождения блокировки по токену владельца
RELEASE_HOST_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

RATELIMIT_TRIAGE_APPLY_KEY = "ratelimit:triage:apply"


class SafetyError(Exception):
    """Базовое исключение защитных механизмов."""

    pass


class HostConcurrencyLockError(SafetyError):
    """Исключение при невозможности захватить эксклюзивный доступ к ПК."""

    def __init__(
        self,
        message: str,
        pc_name: str,
        lock_key: str,
        ttl: int,
    ) -> None:
        super().__init__(message)
        self.pc_name = pc_name
        self.lock_key = lock_key
        self.ttl = ttl


class DeadMansSwitchError(SafetyError):
    """Исключение при превышении аварийного порога массовых операций без подтверждения."""

    def __init__(
        self,
        message: str,
        current_count: int,
        requested_count: int,
        max_limit: int,
        window_seconds: int,
    ) -> None:
        super().__init__(message)
        self.current_count = current_count
        self.requested_count = requested_count
        self.max_limit = max_limit
        self.window_seconds = window_seconds


# ---------------------------------------------------------------------------
# Распределенные блокировки хостов (Host Concurrency Locks)
# ---------------------------------------------------------------------------


def get_canonical_pc_name(pc_name: str) -> str:
    """
    Нормализует имя рабочей станции для единого формата ключа блокировки.
    Устраняет различия в регистрах и опечатках (напр. 'zte1234', 'ЗТЕ-1234' -> 'ZTE1234').
    """
    if not pc_name or not str(pc_name).strip():
        raise ValueError("Имя хоста не может быть пустым")
    raw_str = str(pc_name).strip()
    norm = normalize_pc_name(raw_str)
    if norm:
        return norm.upper()
    return raw_str.upper()


def get_host_lock_key(pc_name: str) -> str:
    """Формирует ключ блокировки в Redis для хоста."""
    canonical = get_canonical_pc_name(pc_name)
    return f"lock:host:{canonical}"


@asynccontextmanager
async def host_concurrency_lock(
    pc_name: str,
    ttl: int | None = None,
    redis_client: Any = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """
    Асинхронный контекстный менеджер для распределенной блокировки конкретного хоста.
    Предотвращает возникновение WinRM/WMI конфликтов (ошибка 0x80338029) при одновременных
    подключениях к одной рабочей станции.

    Пример использования:
        async with host_concurrency_lock("NTEMW0144", ttl=30) as lock_info:
            # Выполнение WinRM/WMI команд
            ...
    """
    lock_ttl = ttl or getattr(settings, "HOST_LOCK_DEFAULT_TTL", 30)
    canonical_pc = get_canonical_pc_name(pc_name)
    lock_key = f"lock:host:{canonical_pc}"
    owner_token = f"{canonical_pc}:{uuid.uuid4().hex}"

    r = redis_client if redis_client is not None else get_redis_client()

    acquired = await r.set(lock_key, owner_token, nx=True, ex=lock_ttl)
    if not acquired:
        logger.warning(
            "Конфликт параллельного доступа: хост %s уже заблокирован другой сессией (ключ %s)",
            canonical_pc,
            lock_key,
        )
        raise HostConcurrencyLockError(
            message=(
                f"Хост '{canonical_pc}' заблокирован другой параллельной сессией "
                f"(защита от WinRM ошибки 0x80338029). Попробуйте позже."
            ),
            pc_name=canonical_pc,
            lock_key=lock_key,
            ttl=lock_ttl,
        )

    logger.debug(
        "Блокировка хоста %s успешно захвачена (owner=%s, ttl=%d)",
        canonical_pc,
        owner_token,
        lock_ttl,
    )
    try:
        yield {
            "pc_name": canonical_pc,
            "lock_key": lock_key,
            "owner": owner_token,
            "ttl": lock_ttl,
        }
    finally:
        try:
            await r.eval(RELEASE_HOST_LOCK_LUA, 1, lock_key, owner_token)
            logger.debug("Блокировка хоста %s освобождена", canonical_pc)
        except Exception as e:
            logger.error(
                "Ошибка освобождения блокировки %s: %s", lock_key, e
            )


async def is_host_locked(pc_name: str, redis_client: Any = None) -> bool:
    """Проверяет, заблокирован ли хост в данный момент."""
    try:
        lock_key = get_host_lock_key(pc_name)
        r = redis_client if redis_client is not None else get_redis_client()
        val = await r.get(lock_key)
        return val is not None
    except Exception as e:
        logger.error(
            "Ошибка проверки статуса блокировки хоста %s: %s", pc_name, e
        )
        return False


async def get_host_lock_info(
    pc_name: str, redis_client: Any = None
) -> dict[str, Any] | None:
    """Возвращает информацию о текущей блокировке хоста или None."""
    try:
        canonical_pc = get_canonical_pc_name(pc_name)
        lock_key = f"lock:host:{canonical_pc}"
        r = redis_client if redis_client is not None else get_redis_client()
        owner = await r.get(lock_key)
        if owner is None:
            return None
        owner_str = (
            owner.decode("utf-8") if isinstance(owner, bytes) else str(owner)
        )
        ttl = await r.ttl(lock_key)
        return {
            "pc_name": canonical_pc,
            "lock_key": lock_key,
            "owner": owner_str,
            "ttl_remaining": ttl,
        }
    except Exception as e:
        logger.error("Ошибка получения информации о блокировке %s: %s", pc_name, e)
        return None


async def release_host_lock_force(
    pc_name: str, redis_client: Any = None
) -> bool:
    """Принудительно снимает блокировку с хоста (для администраторов / экстренного сброса)."""
    try:
        lock_key = get_host_lock_key(pc_name)
        r = redis_client if redis_client is not None else get_redis_client()
        deleted = await r.delete(lock_key)
        return deleted > 0
    except Exception as e:
        logger.error(
            "Ошибка принудительного снятия блокировки хоста %s: %s", pc_name, e
        )
        return False


# ---------------------------------------------------------------------------
# Аварийный тормоз (Dead Man's Switch / Rate Limiter)
# ---------------------------------------------------------------------------


async def check_triage_apply_rate_limit(
    ticket_count: int,
    confirmed_by_human: bool = False,
    max_limit: int | None = None,
    window_seconds: int | None = None,
    redis_client: Any = None,
) -> tuple[bool, int]:
    """
    Проверяет скользящее окно rate-limiter'а для применения решений к заявкам.

    Возвращает:
        (allowed: bool, current_window_count: int)
    """
    if ticket_count <= 0:
        return True, 0

    limit = max_limit or getattr(settings, "TRIAGE_APPLY_MAX_PER_MINUTE", 10)
    window = window_seconds or getattr(
        settings, "TRIAGE_APPLY_RATE_LIMIT_WINDOW", 60
    )
    now = time.time()
    cutoff = now - window

    r = redis_client if redis_client is not None else get_redis_client()

    try:
        # 1. Удаляем устаревшие записи за пределами скользящего окна
        await r.zremrangebyscore(RATELIMIT_TRIAGE_APPLY_KEY, "-inf", cutoff)

        # 2. Получаем текущее число операций в окне
        current_count = await r.zcard(RATELIMIT_TRIAGE_APPLY_KEY) or 0

        # 3. Если нет явного подтверждения оператором и превышен лимит — блокируем
        if not confirmed_by_human and (current_count + ticket_count > limit):
            return False, current_count

        # 4. Регистрируем новые операции
        entries = {
            f"{now}:{uuid.uuid4().hex[:8]}_{i}": now
            for i in range(ticket_count)
        }
        await r.zadd(RATELIMIT_TRIAGE_APPLY_KEY, entries)
        await r.expire(RATELIMIT_TRIAGE_APPLY_KEY, window * 2)

        return True, current_count + ticket_count
    except Exception as e:
        logger.error("Ошибка проверки rate-limit триажа в Redis: %s", e)
        # При сбое Redis без подтверждения для крупных пачек перестраховываемся
        if not confirmed_by_human and ticket_count > limit:
            return False, ticket_count
        return True, ticket_count


async def enforce_triage_apply_rate_limit(
    ticket_count: int,
    confirmed_by_human: bool = False,
    max_limit: int | None = None,
    window_seconds: int | None = None,
    redis_client: Any = None,
) -> int:
    """
    Проверяет лимит и выбрасывает DeadMansSwitchError при превышении порога без подтверждения человека.
    Возвращает новое общее количество операций в окне.
    """
    limit = max_limit or getattr(settings, "TRIAGE_APPLY_MAX_PER_MINUTE", 10)
    window = window_seconds or getattr(
        settings, "TRIAGE_APPLY_RATE_LIMIT_WINDOW", 60
    )

    allowed, current_count = await check_triage_apply_rate_limit(
        ticket_count=ticket_count,
        confirmed_by_human=confirmed_by_human,
        max_limit=limit,
        window_seconds=window,
        redis_client=redis_client,
    )

    if not allowed:
        raise DeadMansSwitchError(
            message=(
                f"Сработал аварийный тормоз (Dead Man's Switch): попытка применения статуса "
                f"к {ticket_count} заявкам превышает лимит ({limit} заявок в минуту, "
                f"текущее использование: {current_count}). Для массовой обработки передайте "
                f"confirmed_by_human=True."
            ),
            current_count=current_count,
            requested_count=ticket_count,
            max_limit=limit,
            window_seconds=window,
        )

    return current_count


async def reset_triage_apply_rate_limit(redis_client: Any = None) -> bool:
    """Сбрасывает скользящее окно rate-limiter'а (для тестов и администрирования)."""
    try:
        r = redis_client if redis_client is not None else get_redis_client()
        await r.delete(RATELIMIT_TRIAGE_APPLY_KEY)
        return True
    except Exception as e:
        logger.error("Ошибка сброса rate limit триажа: %s", e)
        return False
