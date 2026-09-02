"""
Юнит-тесты для модуля защитных механизмов:
1. Distributed Host Concurrency Lock (lock:host:<pc_name>).
2. Dead Man's Switch / Rate Limiter для массовых операций триажа.
3. Интеграция с эндпоинтом POST /api/v1/triage/apply (HTTP 429).
"""

from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.database.db import get_db
from app.main import app
from app.routers.deps import get_service_auth_b64
from app.services.safety import (
    DeadMansSwitchError,
    HostConcurrencyLockError,
    RATELIMIT_TRIAGE_APPLY_KEY,
    RELEASE_HOST_LOCK_LUA,
    check_triage_apply_rate_limit,
    enforce_triage_apply_rate_limit,
    get_canonical_pc_name,
    get_host_lock_info,
    get_host_lock_key,
    host_concurrency_lock,
    is_host_locked,
    release_host_lock_force,
    reset_triage_apply_rate_limit,
)

HEADERS = {"X-Bot-Api-Key": settings.BOT_API_KEY or "test-api-key"}


@pytest.fixture(autouse=True)
def override_deps():
    async def mock_get_service_auth_b64():
        return "bW9ja19hdXRoX2I2NA=="

    async def mock_get_db():
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        yield session

    app.dependency_overrides[get_service_auth_b64] = mock_get_service_auth_b64
    app.dependency_overrides[get_db] = mock_get_db
    yield
    app.dependency_overrides.clear()


# ===========================================================================
# 1. Тесты Distributed Host Concurrency Lock
# ===========================================================================


def test_pc_name_canonicalization():
    """Проверка корректной нормализации имён хостов для ключей блокировки."""
    assert get_canonical_pc_name("ntemw0144") == "NTEMW0144"
    assert get_canonical_pc_name("zte1234") == "ZTE1234"
    assert get_canonical_pc_name("ЗТЕ1234") == "ZTE1234"
    assert get_canonical_pc_name("  kmk0050  ") == "KMK0050"
    assert get_host_lock_key("ntemw0144") == "lock:host:NTEMW0144"

    with pytest.raises(ValueError, match="не может быть пустым"):
        get_canonical_pc_name("")

    with pytest.raises(ValueError, match="не может быть пустым"):
        get_canonical_pc_name("   ")


@pytest.mark.asyncio
async def test_host_concurrency_lock_acquire_and_release():
    """Проверка успешного захвата и освобождения блокировки хоста."""
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock(return_value=1)

    async with host_concurrency_lock(
        "NTEMW0144", ttl=30, redis_client=mock_redis
    ) as lock_info:
        assert lock_info["pc_name"] == "NTEMW0144"
        assert lock_info["lock_key"] == "lock:host:NTEMW0144"
        assert lock_info["ttl"] == 30
        assert lock_info["owner"].startswith("NTEMW0144:")

        # Проверяем параметры вызова set (SET NX EX)
        mock_redis.set.assert_called_once_with(
            "lock:host:NTEMW0144", lock_info["owner"], nx=True, ex=30
        )

    # Проверяем вызов безопасного освобождения через Lua скрипт
    mock_redis.eval.assert_called_once_with(
        RELEASE_HOST_LOCK_LUA, 1, "lock:host:NTEMW0144", lock_info["owner"]
    )


@pytest.mark.asyncio
async def test_host_concurrency_lock_conflict():
    """Проверка выброса исключения HostConcurrencyLockError при занятом хосте."""
    mock_redis = AsyncMock()
    # Имитируем, что замок уже занят другой сессией (SET NX вернул False/None)
    mock_redis.set = AsyncMock(return_value=False)

    with pytest.raises(HostConcurrencyLockError) as exc_info:
        async with host_concurrency_lock(
            "NTEMW0144", ttl=30, redis_client=mock_redis
        ):
            pass

    err = exc_info.value
    assert err.pc_name == "NTEMW0144"
    assert err.lock_key == "lock:host:NTEMW0144"
    assert err.ttl == 30
    assert "0x80338029" in str(err)
    # eval не должен вызываться, если замок не был захвачен
    mock_redis.eval.assert_not_called()


@pytest.mark.asyncio
async def test_host_concurrency_lock_release_on_exception():
    """Проверка гарантированного освобождения замка при ошибке внутри блока."""
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock(return_value=1)

    with pytest.raises(RuntimeError, match="Internal session crash"):
        async with host_concurrency_lock(
            "KZMK0010", ttl=45, redis_client=mock_redis
        ) as lock_info:
            raise RuntimeError("Internal session crash")

    mock_redis.eval.assert_called_once_with(
        RELEASE_HOST_LOCK_LUA, 1, "lock:host:KZMK0010", lock_info["owner"]
    )


@pytest.mark.asyncio
async def test_is_host_locked_and_info():
    """Проверка функций проверки статуса блокировки и получения метаданных."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="NTEMW0144:owner123")
    mock_redis.ttl = AsyncMock(return_value=25)

    locked = await is_host_locked("ntemw0144", redis_client=mock_redis)
    assert locked is True
    mock_redis.get.assert_called_with("lock:host:NTEMW0144")

    info = await get_host_lock_info("ntemw0144", redis_client=mock_redis)
    assert info is not None
    assert info["pc_name"] == "NTEMW0144"
    assert info["owner"] == "NTEMW0144:owner123"
    assert info["ttl_remaining"] == 25

    # Когда хост не заблокирован
    mock_redis.get = AsyncMock(return_value=None)
    locked_free = await is_host_locked("ntemw0144", redis_client=mock_redis)
    assert locked_free is False

    info_free = await get_host_lock_info("ntemw0144", redis_client=mock_redis)
    assert info_free is None


@pytest.mark.asyncio
async def test_release_host_lock_force():
    """Проверка принудительного снятия блокировки хоста."""
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock(return_value=1)

    res = await release_host_lock_force("zte1234", redis_client=mock_redis)
    assert res is True
    mock_redis.delete.assert_called_once_with("lock:host:ZTE1234")


# ===========================================================================
# 2. Тесты Dead Man's Switch / Rate Limiter
# ===========================================================================


@pytest.mark.asyncio
async def test_rate_limit_under_threshold():
    """Проверка пропуска операций в пределах допустимого лимита (< 10 заявок/мин)."""
    mock_redis = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock(return_value=0)
    mock_redis.zcard = AsyncMock(return_value=4)
    mock_redis.zadd = AsyncMock(return_value=3)
    mock_redis.expire = AsyncMock(return_value=True)

    allowed, count = await check_triage_apply_rate_limit(
        ticket_count=3,
        confirmed_by_human=False,
        max_limit=10,
        window_seconds=60,
        redis_client=mock_redis,
    )
    assert allowed is True
    assert count == 7  # 4 + 3
    mock_redis.zadd.assert_called_once()
    mock_redis.expire.assert_called_once_with(RATELIMIT_TRIAGE_APPLY_KEY, 120)


@pytest.mark.asyncio
async def test_rate_limit_exceeded_without_confirmation():
    """Проверка блокировки операций при превышении лимита без флага подтверждения."""
    mock_redis = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock(return_value=0)
    mock_redis.zcard = AsyncMock(return_value=8)

    # 8 + 4 = 12 > 10 -> отказ
    allowed, count = await check_triage_apply_rate_limit(
        ticket_count=4,
        confirmed_by_human=False,
        max_limit=10,
        window_seconds=60,
        redis_client=mock_redis,
    )
    assert allowed is False
    assert count == 8
    mock_redis.zadd.assert_not_called()

    # enforce должен выбросить DeadMansSwitchError
    with pytest.raises(DeadMansSwitchError) as exc_info:
        await enforce_triage_apply_rate_limit(
            ticket_count=4,
            confirmed_by_human=False,
            max_limit=10,
            window_seconds=60,
            redis_client=mock_redis,
        )

    err = exc_info.value
    assert err.current_count == 8
    assert err.requested_count == 4
    assert err.max_limit == 10
    assert "Dead Man's Switch" in str(err)
    assert "confirmed_by_human=True" in str(err)


@pytest.mark.asyncio
async def test_rate_limit_bypass_with_human_confirmation():
    """Проверка обхода аварийного лимита при явном флаге confirmed_by_human=True."""
    mock_redis = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock(return_value=0)
    mock_redis.zcard = AsyncMock(return_value=15)
    mock_redis.zadd = AsyncMock(return_value=20)
    mock_redis.expire = AsyncMock(return_value=True)

    # 15 + 20 = 35 > 10, но confirmed_by_human=True -> разрешено
    allowed, count = await check_triage_apply_rate_limit(
        ticket_count=20,
        confirmed_by_human=True,
        max_limit=10,
        window_seconds=60,
        redis_client=mock_redis,
    )
    assert allowed is True
    assert count == 35
    mock_redis.zadd.assert_called_once()

    # enforce не должен выбрасывать исключение
    res_count = await enforce_triage_apply_rate_limit(
        ticket_count=20,
        confirmed_by_human=True,
        max_limit=10,
        window_seconds=60,
        redis_client=mock_redis,
    )
    assert res_count == 35


@pytest.mark.asyncio
async def test_rate_limit_zero_or_negative_tickets():
    """Проверка граничных условий с нулевым количеством заявок."""
    mock_redis = AsyncMock()
    allowed, count = await check_triage_apply_rate_limit(
        ticket_count=0, redis_client=mock_redis
    )
    assert allowed is True
    assert count == 0
    mock_redis.zcard.assert_not_called()


@pytest.mark.asyncio
async def test_reset_triage_apply_rate_limit():
    """Проверка сброса кэша rate-limiter'а."""
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock(return_value=1)

    res = await reset_triage_apply_rate_limit(redis_client=mock_redis)
    assert res is True
    mock_redis.delete.assert_called_once_with(RATELIMIT_TRIAGE_APPLY_KEY)


# ===========================================================================
# 3. Интеграционные тесты эндпоинта POST /api/v1/triage/apply
# ===========================================================================


@pytest.mark.asyncio
async def test_api_apply_rate_limit_allows_under_threshold():
    """Эндпоинт /apply успешно выполняет пакетную операцию в пределах лимита."""
    with patch(
        "app.routers.triage.enforce_triage_apply_rate_limit",
        new_callable=AsyncMock,
        return_value=2,
    ) as mock_enforce, patch(
        "app.services.intraservice.update_task_full",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.intraservice.add_task_expenses",
        new_callable=AsyncMock,
        return_value=True,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/triage/apply",
                headers=HEADERS,
                json={
                    "task_ids": [140001, 140002],
                    "status_id": 29,
                    "comment": "Выполнено успешно",
                    "expenses": 15,
                    "confirmed_by_human": False,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["results"]) == 2
            mock_enforce.assert_called_once_with(
                ticket_count=2, confirmed_by_human=False
            )


@pytest.mark.asyncio
async def test_api_apply_rate_limit_triggers_429():
    """Эндпоинт /apply возвращает HTTP 429 при срабатывании аварийного тормоза Dead Man's Switch."""
    with patch(
        "app.routers.triage.enforce_triage_apply_rate_limit",
        new_callable=AsyncMock,
        side_effect=DeadMansSwitchError(
            message="Превышен лимит 10 заявок/мин",
            current_count=9,
            requested_count=5,
            max_limit=10,
            window_seconds=60,
        ),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/triage/apply",
                headers=HEADERS,
                json={
                    "task_ids": [140001, 140002, 140003, 140004, 140005],
                    "status_id": 30,
                    "comment": "Массовая отмена",
                    "confirmed_by_human": False,
                },
            )
            assert resp.status_code == 429
            data = resp.json()
            assert "detail" in data
            assert "Превышен лимит 10 заявок/мин" in data["detail"]


@pytest.mark.asyncio
async def test_api_apply_mass_tickets_with_human_confirmation():
    """Эндпоинт /apply разрешает массовое закрытие >10 заявок с флагом confirmed_by_human=True."""
    tasks = list(range(140001, 140016))  # 15 заявок
    with patch(
        "app.routers.triage.enforce_triage_apply_rate_limit",
        new_callable=AsyncMock,
        return_value=15,
    ) as mock_enforce, patch(
        "app.services.intraservice.update_task_full",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.intraservice.add_task_expenses",
        new_callable=AsyncMock,
        return_value=True,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/triage/apply",
                headers=HEADERS,
                json={
                    "task_ids": tasks,
                    "status_id": 29,
                    "comment": "Массовое закрытие инженером",
                    "confirmed_by_human": True,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["results"]) == 15
            mock_enforce.assert_called_once_with(
                ticket_count=15, confirmed_by_human=True
            )


@pytest.mark.asyncio
async def test_api_apply_dry_run_bypasses_rate_limit():
    """В режиме dry_run rate-limiter не вызывается."""
    with patch(
        "app.routers.triage.enforce_triage_apply_rate_limit",
        new_callable=AsyncMock,
    ) as mock_enforce:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/triage/apply",
                headers=HEADERS,
                json={
                    "task_ids": [140001, 140002, 140003],
                    "status_id": 29,
                    "comment": "Симуляция",
                    "dry_run": True,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["results"]) == 3
            assert data["results"][0]["status"] == "simulated"
            mock_enforce.assert_not_called()
