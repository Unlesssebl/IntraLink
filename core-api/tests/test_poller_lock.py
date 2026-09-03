"""
Тесты для распределенного замка Leader Lock и логики Poller Daemon.
"""
from unittest.mock import AsyncMock, patch
import pytest

from app.poller import IntraServicePoller, LEADER_LOCK_KEY, LEADER_LOCK_TTL


@pytest.mark.asyncio
async def test_poller_leader_lock_acquisition_and_renewal():
    """Проверка захвата и продления статуса лидера опроса."""
    poller = IntraServicePoller()
    mock_redis = AsyncMock()

    # 1. Первый захват замка (SET NX EX)
    mock_redis.set = AsyncMock(return_value=True)
    is_leader = await poller._try_acquire_leader_lock(mock_redis)
    assert is_leader is True
    assert poller._is_leader is True
    mock_redis.set.assert_called_once_with(
        LEADER_LOCK_KEY, poller.worker_id, nx=True, ex=LEADER_LOCK_TTL
    )

    # 2. Продление замка (замок уже наш)
    mock_redis.set = AsyncMock(return_value=False)
    mock_redis.get = AsyncMock(return_value=poller.worker_id)
    is_leader_renewed = await poller._try_acquire_leader_lock(mock_redis)
    assert is_leader_renewed is True
    mock_redis.expire.assert_called_once_with(LEADER_LOCK_KEY, LEADER_LOCK_TTL)

    # 3. Замок перехвачен другим воркером
    mock_redis.set = AsyncMock(return_value=False)
    mock_redis.get = AsyncMock(return_value="poller:other_host:1234")
    is_leader_lost = await poller._try_acquire_leader_lock(mock_redis)
    assert is_leader_lost is False
    assert poller._is_leader is False


@pytest.mark.asyncio
async def test_poller_release_leader_lock():
    """Проверка освобождения замка при остановке воркера."""
    poller = IntraServicePoller()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=poller.worker_id)

    await poller._release_leader_lock(mock_redis)
    mock_redis.delete.assert_called_once_with(LEADER_LOCK_KEY)
