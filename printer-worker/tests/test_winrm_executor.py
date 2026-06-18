import os
import pytest
import asyncio
from unittest.mock import patch, MagicMock
import winrm.exceptions
import requests.exceptions

# Устанавливаем переменные окружения до импорта
os.environ["BOT_API_KEY"] = "dummy_key"
os.environ["WINRM_USERNAME"] = "dummy_user"
os.environ["WINRM_PASSWORD"] = "dummy_pass"
os.environ["WINRM_TRANSPORT"] = "ntlm"

from executors.winrm_executor import WinRMExecutor


@pytest.mark.asyncio
async def test_winrm_run_powershell_success():
    executor = WinRMExecutor()

    mock_rs = MagicMock()
    mock_rs.status_code = 0
    mock_rs.std_out = b"Hello from remote PC"
    mock_rs.std_err = b""

    mock_session = MagicMock()
    mock_session.run_ps.return_value = mock_rs

    with patch("executors.winrm_executor.winrm.Session", return_value=mock_session):
        status, stdout, stderr = await executor.run_powershell(
            "target-pc", "Write-Output 'Hello'"
        )
        assert status == 0
        assert stdout == "Hello from remote PC"
        assert stderr == ""


@pytest.mark.asyncio
async def test_winrm_run_powershell_retry_then_success():
    executor = WinRMExecutor()

    mock_rs = MagicMock()
    mock_rs.status_code = 0
    mock_rs.std_out = b"Success after retry"
    mock_rs.std_err = b""

    mock_session = MagicMock()
    # Первая попытка - ошибка подключения, вторая - успех
    mock_session.run_ps.side_effect = [
        winrm.exceptions.WinRMTransportError("http", "Connection refused"),
        mock_rs,
    ]

    with (
        patch("executors.winrm_executor.winrm.Session", return_value=mock_session),
        patch("time.sleep") as mock_sleep,
    ):
        status, stdout, stderr = await executor.run_powershell(
            "target-pc", "Write-Output 'Retry'"
        )
        assert status == 0
        assert stdout == "Success after retry"
        assert mock_sleep.call_count == 1


@pytest.mark.asyncio
async def test_winrm_run_powershell_failure_all_attempts():
    executor = WinRMExecutor()

    mock_session = MagicMock()
    mock_session.run_ps.side_effect = requests.exceptions.RequestException(
        "Connection timeout"
    )

    with (
        patch("executors.winrm_executor.winrm.Session", return_value=mock_session),
        patch("time.sleep") as mock_sleep,
    ):
        status, stdout, stderr = await executor.run_powershell(
            "target-pc", "Write-Output 'Fail'"
        )
        assert status == -1
        assert "Connection timeout" in stderr
        assert mock_sleep.call_count == 2


@pytest.mark.asyncio
async def test_winrm_run_powershell_timeout():
    executor = WinRMExecutor()

    # Симулируем долгое выполнение, чтобы asyncio.wait_for выбросил TimeoutError
    async def slow_thread(*args, **kwargs):
        await asyncio.sleep(10)
        return 0, "", ""

    with patch("asyncio.to_thread", side_effect=slow_thread):
        status, stdout, stderr = await executor.run_powershell(
            "target-pc", "Write-Output 'Slow'", timeout=0.1
        )
        assert status == -1
        assert "Таймаут выполнения команды" in stderr
