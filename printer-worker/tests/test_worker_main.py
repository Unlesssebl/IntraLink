import os
import json
import pytest
import logging
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

# Устанавливаем переменные окружения до импорта
os.environ["BOT_API_KEY"] = "dummy_key"
os.environ["REDIS_URL"] = "redis://localhost:6379"

# Создаем временный файл базы знаний для тестов
TEST_KB_CONTENT = {
    "printers": [
        {
            "model_key": "test_model",
            "display_name": "Test Printer",
            "driver_name": "Test Driver",
            "driver_inf_path": "\\\\srv\\share\\test.inf",
            "vendor": "TestVendor",
            "connection_type": "tcpip"
        }
    ],
    "printer_name_prefixes": ["ittp"]
}

@pytest.fixture(autouse=True)
def setup_kb_env(tmp_path):
    kb_file = tmp_path / "printers_kb.json"
    kb_file.write_text(json.dumps(TEST_KB_CONTENT), encoding="utf-8")
    
    # Очищаем кэшированные глобальные переменные в worker_main
    import worker_main
    worker_main._kb = None
    worker_main._orchestrator = None
    
    with patch("worker_main.PRINTERS_KB_PATH", str(kb_file)):
        yield

import worker_main

@pytest.mark.asyncio
async def test_get_kb():
    kb = worker_main.get_kb()
    assert kb is not None
    assert len(kb.printers) == 1
    assert kb.printers[0].model_key == "test_model"
    
    # Проверяем, что кэш работает и возвращает тот же объект
    kb2 = worker_main.get_kb()
    assert kb is kb2

@pytest.mark.asyncio
async def test_get_orchestrator():
    orch = worker_main.get_orchestrator()
    assert orch is not None
    assert orch.kb is worker_main.get_kb()

@pytest.mark.asyncio
async def test_start_heartbeat():
    mock_redis_client = AsyncMock()
    mock_redis_client.setex = AsyncMock()
    
    with (
        patch("worker_services.redis_listener.get_redis", return_value=mock_redis_client),
        patch("asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError)
    ):
        try:
            await worker_main.start_heartbeat()
        except asyncio.CancelledError:
            pass
        mock_redis_client.setex.assert_called_once_with("printer_worker:status", 10, "online")

@pytest.mark.asyncio
async def test_redis_log_handler():
    from orchestrator.orchestrator import current_task_id
    mock_redis_client = AsyncMock()
    mock_redis_client.publish = AsyncMock()
    mock_redis_client.rpush = AsyncMock()
    mock_redis_client.expire = AsyncMock()
    
    # Подменяем get_redis для RedisLogHandler
    with patch("worker_services.redis_listener.get_redis", return_value=mock_redis_client):
        loop = asyncio.get_running_loop()
        handler = worker_main.RedisLogHandler(loop=loop)
        
        # Настраиваем контекст
        token = current_task_id.set(9999)
        try:
            record = logging.LogRecord(
                name="test_logger",
                level=logging.INFO,
                pathname="test.py",
                lineno=10,
                msg="Test log message to redis",
                args=(),
                exc_info=None
            )
            handler.emit(record)
            
            # Ждем выполнения таски в фоне
            await asyncio.sleep(0.1)
            
            mock_redis_client.publish.assert_called_once()
            mock_redis_client.rpush.assert_called_once()
            mock_redis_client.expire.assert_called_once_with("printer_job_logs_history:9999", 2592000)
            
            args = mock_redis_client.publish.call_args[0]
            assert args[0] == "printer_job_logs:9999"
            assert "Test log message to redis" in args[1]
        finally:
            current_task_id.reset(token)

@pytest.mark.asyncio
async def test_main_lifecycle():
    # Симулируем отмену в методе stop_event.wait(), чтобы проверить фазу остановки в main
    mock_stop_event = MagicMock()
    mock_stop_event.wait = AsyncMock(side_effect=asyncio.CancelledError)
    
    async def dummy_task():
        pass
    
    with (
        patch("asyncio.Event", return_value=mock_stop_event),
        patch("worker_main.init_session", new_callable=AsyncMock) as mock_init,
        patch("worker_main.close_session", new_callable=AsyncMock) as mock_close,
        patch("worker_main.start_redis_listener", side_effect=dummy_task) as mock_listener,
        patch("worker_main.start_heartbeat", side_effect=dummy_task) as mock_heartbeat,
    ):
        await worker_main.main()
        
        mock_init.assert_called_once()
        mock_close.assert_called_once()
        mock_listener.assert_called_once()
        mock_heartbeat.assert_called_once()
