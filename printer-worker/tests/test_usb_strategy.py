import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

# Устанавливаем переменные окружения до импорта
os.environ["BOT_API_KEY"] = "dummy_key"

from orchestrator.schemas import PrintJob, JobState, ConnectionType, PrinterDriverInfo
from strategies.usb_strategy import UsbDiscoveryStrategy


@pytest.fixture
def mock_kb():
    kb = MagicMock()
    driver = PrinterDriverInfo(
        model_key="test_usb_printer",
        display_name="Test USB Printer",
        driver_name="Test USB Driver",
        driver_inf_path="\\\\srv\\share\\test_usb.inf",
        vendor="TestVendor",
        connection_type=ConnectionType.USB,
    )
    kb.find_by_hw_id.return_value = driver
    return kb


@pytest.mark.asyncio
async def test_usb_probe_no_target_pc():
    strategy = UsbDiscoveryStrategy()
    job = PrintJob(task_id=101, tg_user_id=222, raw_text="USB")
    res = await strategy.probe(job)
    assert res.state == JobState.FAILED
    assert "компьютер не указан" in res.error_message


@pytest.mark.asyncio
async def test_usb_probe_printer_not_found():
    strategy = UsbDiscoveryStrategy()
    job = PrintJob(task_id=101, tg_user_id=222, raw_text="USB", target_pc="PC-TEST")

    with patch(
        "strategies.usb_strategy.winrm_executor.run_powershell", new_callable=AsyncMock
    ) as mock_ps:
        # Симулируем пустой вывод WinRM (принтер не найден)
        mock_ps.return_value = (0, "", "")
        res = await strategy.probe(job)
        assert res.state == JobState.WAITING
        assert "Кабель USB принтера не подключен" in res.error_message


@pytest.mark.asyncio
async def test_usb_probe_success(mock_kb):
    strategy = UsbDiscoveryStrategy(kb=mock_kb)
    job = PrintJob(task_id=101, tg_user_id=222, raw_text="USB", target_pc="PC-TEST")

    with patch(
        "strategies.usb_strategy.winrm_executor.run_powershell", new_callable=AsyncMock
    ) as mock_ps:
        # Симулируем успешный поиск с выводом Hardware ID
        mock_ps.return_value = (0, "USBPRINT\\HEWLETT-PACKARDHP_LAFFCC\n", "")
        res = await strategy.probe(job)
        assert res.model_key == "test_usb_printer"
        assert res.driver_info.display_name == "Test USB Printer"
        assert res.connection_type == ConnectionType.USB
        mock_kb.find_by_hw_id.assert_called_once_with(
            "USBPRINT\\HEWLETT-PACKARDHP_LAFFCC"
        )


@pytest.mark.asyncio
async def test_usb_probe_driver_not_found_in_kb(mock_kb):
    # Настраиваем БЗ так, чтобы драйвер не нашелся
    mock_kb.find_by_hw_id.return_value = None
    strategy = UsbDiscoveryStrategy(kb=mock_kb)
    job = PrintJob(task_id=101, tg_user_id=222, raw_text="USB", target_pc="PC-TEST")

    with patch(
        "strategies.usb_strategy.winrm_executor.run_powershell", new_callable=AsyncMock
    ) as mock_ps:
        mock_ps.return_value = (0, "USBPRINT\\UNKNOWN_HW_ID\n", "")
        res = await strategy.probe(job)
        assert res.state == JobState.FAILED
        assert "не найдено в Базе Знаний" in res.error_message


@pytest.mark.asyncio
async def test_usb_execute_success(mock_kb):
    strategy = UsbDiscoveryStrategy(kb=mock_kb)
    driver = mock_kb.find_by_hw_id.return_value

    job = PrintJob(
        task_id=101,
        tg_user_id=222,
        raw_text="USB",
        target_pc="PC-TEST",
        driver_info=driver,
    )

    with (
        patch(
            "strategies.usb_strategy.smb_executor.check_source_accessible",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "strategies.usb_strategy.smb_executor.copy_driver_to_temp",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "strategies.usb_strategy.winrm_executor.run_powershell",
            new_callable=AsyncMock,
        ) as mock_ps,
        patch(
            "worker_services.redis_listener.save_job_state", new_callable=AsyncMock
        ) as mock_save_state,
    ):
        # 1-й вызов: pnputil /add-driver (успех)
        # 2-й вызов: Get-CimInstance (обнаружен принтер)
        mock_ps.side_effect = [
            (0, "Driver added", ""),
            (0, '{"Name": "Test USB Printer"}', ""),
        ]

        # Переопределяем параметры ожидания, чтобы тесты шли быстро
        with (
            patch("strategies.usb_strategy._PNP_POLL_INTERVAL", 0.01),
            patch("strategies.usb_strategy._PNP_POLL_TIMEOUT", 0.05),
        ):
            res = await strategy.execute(job)
            assert res.state == JobState.DONE
            assert res.error_message is None
            assert mock_ps.call_count == 2
            mock_save_state.assert_called()


@pytest.mark.asyncio
async def test_usb_execute_source_inaccessible(mock_kb):
    strategy = UsbDiscoveryStrategy(kb=mock_kb)
    driver = mock_kb.find_by_hw_id.return_value
    job = PrintJob(
        task_id=101,
        tg_user_id=222,
        raw_text="USB",
        target_pc="PC-TEST",
        driver_info=driver,
    )

    with (
        patch(
            "strategies.usb_strategy.smb_executor.check_source_accessible",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("worker_services.redis_listener.save_job_state", new_callable=AsyncMock),
    ):
        res = await strategy.execute(job)
        assert res.state == JobState.FAILED
        assert "Источник драйвера недоступен" in res.error_message


@pytest.mark.asyncio
async def test_usb_execute_copy_failure(mock_kb):
    strategy = UsbDiscoveryStrategy(kb=mock_kb)
    driver = mock_kb.find_by_hw_id.return_value
    job = PrintJob(
        task_id=101,
        tg_user_id=222,
        raw_text="USB",
        target_pc="PC-TEST",
        driver_info=driver,
    )

    with (
        patch(
            "strategies.usb_strategy.smb_executor.check_source_accessible",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "strategies.usb_strategy.smb_executor.copy_driver_to_temp",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("worker_services.redis_listener.save_job_state", new_callable=AsyncMock),
    ):
        res = await strategy.execute(job)
        assert res.state == JobState.FAILED
        assert "Не удалось скопировать драйвер" in res.error_message


@pytest.mark.asyncio
async def test_usb_execute_pnp_timeout_retry_rescan(mock_kb):
    strategy = UsbDiscoveryStrategy(kb=mock_kb)
    driver = mock_kb.find_by_hw_id.return_value
    job = PrintJob(
        task_id=101,
        tg_user_id=222,
        raw_text="USB",
        target_pc="PC-TEST",
        driver_info=driver,
    )

    with (
        patch(
            "strategies.usb_strategy.smb_executor.check_source_accessible",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "strategies.usb_strategy.smb_executor.copy_driver_to_temp",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "strategies.usb_strategy.winrm_executor.run_powershell",
            new_callable=AsyncMock,
        ) as mock_ps,
        patch("worker_services.redis_listener.save_job_state", new_callable=AsyncMock),
    ):
        # 1-й: pnputil /add-driver (успех)
        # 2-й: Get-CimInstance (нет принтера, 1-я итерация первого ожидания)
        # 3-й: Get-CimInstance (нет принтера, 2-я итерация первого ожидания)
        # 4-й: pnputil /scan-devices (рескан)
        # 5-й: Get-CimInstance (обнаружен принтер после рескана)
        mock_ps.side_effect = [
            (0, "Driver added", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "Rescanned", ""),
            (0, '{"Name": "Test USB Printer"}', ""),
        ]

        with (
            patch("strategies.usb_strategy._PNP_POLL_INTERVAL", 0.01),
            patch("strategies.usb_strategy._PNP_POLL_TIMEOUT", 0.02),
        ):
            res = await strategy.execute(job)
            assert res.state == JobState.DONE
            assert mock_ps.call_count == 5
