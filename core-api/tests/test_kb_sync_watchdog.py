import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from shared.json_utils import json_dumps
from app.services.rag import get_kb_sync_progress, _save_sync_progress


@pytest.mark.asyncio
async def test_get_kb_sync_progress_healthy():
    """Тест: если процесс активен и updated_at свежий (<300с), статус не сбрасывается."""
    mock_redis = AsyncMock()
    now_iso = datetime.now(timezone.utc).isoformat()
    state = {
        "is_running": True,
        "percent": 45,
        "processed_roots": 3,
        "total_roots": 15,
        "updated_at": now_iso,
        "error": None,
    }
    mock_redis.get.return_value = json_dumps(state).encode("utf-8")

    with patch("app.services.rag._get_redis_safe", return_value=mock_redis):
        res = await get_kb_sync_progress()
        assert res["is_running"] is True
        assert res["error"] is None
        assert res["percent"] == 45
        mock_redis.delete.assert_not_called()


@pytest.mark.asyncio
async def test_get_kb_sync_progress_watchdog_timeout():
    """Тест: если updated_at старше 300с, сторожевой таймер корректно фиксирует прерывание и сбрасывает лок."""
    mock_redis = AsyncMock()
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
    state = {
        "is_running": True,
        "percent": 0,
        "processed_roots": 0,
        "total_roots": 15,
        "updated_at": stale_time,
        "error": None,
    }
    mock_redis.get.return_value = json_dumps(state).encode("utf-8")

    with patch("app.services.rag._get_redis_safe", return_value=mock_redis):
        res = await get_kb_sync_progress()
        assert res["is_running"] is False
        assert "Процесс синхронизации был прерван" in res["error"]
        mock_redis.delete.assert_called_once_with("lock:kb_sync")


@pytest.mark.asyncio
async def test_save_sync_progress_updates_heartbeat_and_refreshes_lock():
    """Тест: _save_sync_progress обновляет updated_at и продлевает TTL замка lock:kb_sync."""
    mock_redis = AsyncMock()
    old_time = "2026-01-01T00:00:00+00:00"
    state = {
        "is_running": True,
        "percent": 10,
        "updated_at": old_time,
    }

    await _save_sync_progress(mock_redis, state)

    assert state["updated_at"] != old_time
    # Проверяем, что updated_at в формате ISO UTC и недавний
    upd = datetime.fromisoformat(state["updated_at"])
    assert (datetime.now(timezone.utc) - upd).total_seconds() < 5

    mock_redis.set.assert_called_once()
    mock_redis.expire.assert_called_once_with("lock:kb_sync", 1800)
