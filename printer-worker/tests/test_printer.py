import asyncio
import logging
import sys
import os
from unittest.mock import patch, AsyncMock, MagicMock

# Добавляем пути, чтобы импорты работали
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("test_install")

from orchestrator.schemas import PrintJob, JobState, ConnectionType  # noqa: E402
from strategies import get_strategy  # noqa: E402
from executors.wmi_executor import WMIExecutor  # noqa: E402
import worker_config as config  # noqa: E402


async def test_install():
    target_pc = "itt0024"
    model_key = "kyocera_ecosys_m2040dn"  # ключ из KB

    logger.info("Загрузка базы знаний...")
    import json

    kb_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "knowledge_base",
        "printers_knowledge_base.json",
    )
    with open(kb_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    from orchestrator.schemas import KnowledgeBase

    kb = KnowledgeBase.model_validate(data)

    driver_info = kb.find_by_key(model_key)
    assert driver_info is not None, f"Драйвер {model_key} не найден!"

    logger.info(f"Найден драйвер: {driver_info.display_name}")

    # Формируем фиктивную задачу
    job = PrintJob(
        task_id=99999,
        tg_user_id=123,
        raw_text="Тестовая установка",
        state=JobState.PROBING,
        target_pc=target_pc,
        model_key=model_key,
        connection_type=ConnectionType.TCPIP,
        printer_address="ittp0000",
        driver_info=driver_info,
    )

    domain = ""
    username = config.WINRM_USERNAME
    if "\\" in username:
        domain, username = username.split("\\", 1)

    # Задаем моки
    with (
        patch(
            "executors.wmi_executor.WMIExecutor.enable_winrm", new_callable=AsyncMock
        ) as mock_enable_winrm,
        patch(
            "executors.wmi_executor.WMIExecutor.disable_winrm", new_callable=AsyncMock
        ) as mock_disable_winrm,
        patch(
            "strategies.tcpip_strategy.winrm_executor.run_powershell",
            new_callable=AsyncMock,
        ) as mock_run_ps,
        patch(
            "strategies.tcpip_strategy.smb_executor.copy_driver_to_temp",
            new_callable=AsyncMock,
        ) as mock_copy_smb,
        patch(
            "strategies.tcpip_strategy.smb_executor.check_source_accessible",
            new_callable=AsyncMock,
        ) as mock_check_source,
    ):
        # Настраиваем возвращаемые значения для run_powershell
        # 1-й вызов (ручной probe в тесте): Get-PrinterDriver. Вернем status=1 (драйвер отсутствует)
        # 2-й вызов (execute -> install_driver_script): pnputil
        # 3-й вызов (execute -> port_script): Add-PrinterPort
        # 4-й вызов (execute -> printer_script): Add-Printer
        # 5-й вызов (execute -> verify_script): Get-Printer (Verification)
        mock_run_ps.side_effect = [
            (1, "Driver not found", ""),  # Get-PrinterDriver (manual probe)
            (0, "Driver installed", ""),  # pnputil
            (0, "Port added", ""),  # Add-PrinterPort
            (0, "Printer added", ""),  # Add-Printer
            (0, driver_info.display_name, ""),  # Get-Printer (Verification)
        ]

        mock_copy_smb.return_value = True
        mock_check_source.return_value = True

        assert job.target_pc is not None
        wmi_exec = WMIExecutor(
            target_ip=job.target_pc,
            username=username,
            password=config.WINRM_PASSWORD,
            domain=domain,
        )

        logger.info("Попытка включения WinRM через WMI...")
        await wmi_exec.enable_winrm()
        logger.info("WinRM успешно включен!")

        try:
            assert job.connection_type is not None
            strategy = get_strategy(job.connection_type)
            logger.info("Начало выполнения стратегии установки...")

            # probe
            job = await strategy.probe(job)
            assert job.state != JobState.FAILED, (
                f"Диагностика не пройдена: {job.error_message}"
            )

            # execute
            job = await strategy.execute(job)
            assert job.state == JobState.DONE, (
                f"Установка завершилась с ошибкой: {job.error_message}"
            )
            logger.info("Установка успешно завершена!")

        finally:
            logger.info("Отключение WinRM...")
            await wmi_exec.disable_winrm()
            logger.info("WinRM успешно отключен!")

        # Проверим, что моки вызывались
        mock_enable_winrm.assert_called_once()
        mock_disable_winrm.assert_called_once()
        mock_copy_smb.assert_called_once()
        assert mock_run_ps.call_count == 5


async def test_wmi_executor_wait_for_port():
    executor = WMIExecutor("127.0.0.1", "user", "pass")

    mock_writer = AsyncMock()
    # close() в StreamWriter не является сорутиной, поэтому мокаем его как обычный MagicMock
    mock_writer.close = MagicMock()
    # Мокаем open_connection, чтобы он возвращал фейковый reader и writer
    with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = (AsyncMock(), mock_writer)
        result = await executor._wait_for_port(5985, timeout=2.0)
        assert result is True
        mock_connect.assert_called_once_with("127.0.0.1", 5985)
        mock_writer.close.assert_called_once()


async def test_wmi_executor_wait_for_port_timeout():
    executor = WMIExecutor("127.0.0.1", "user", "pass")

    # Мокаем open_connection на ошибку и sleep, чтобы тест не висел
    with (
        patch("asyncio.open_connection", side_effect=OSError) as mock_connect,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await executor._wait_for_port(5985, timeout=2.0)
        assert result is False
        assert mock_connect.call_count > 0


async def test_wmi_executor_read_bootstrap_log_success():
    executor = WMIExecutor("127.0.0.1", "user", "pass")

    mock_file = MagicMock()
    mock_file.read.return_value = b"Test log output"
    mock_file.__enter__.return_value = mock_file

    with (
        patch("smbclient.register_session") as mock_register,
        patch("smbclient.open_file", return_value=mock_file) as mock_open,
    ):
        result = executor._read_bootstrap_log_sync()
        assert result == "Test log output"
        mock_register.assert_called_once_with("127.0.0.1", username="user", password="pass")
        mock_open.assert_called_once_with("\\\\127.0.0.1\\C$\\Windows\\Temp\\wmi_bootstrap.log", mode="rb")


async def test_wmi_executor_read_bootstrap_log_failure():
    executor = WMIExecutor("127.0.0.1", "user", "pass")

    with (
        patch("smbclient.register_session", side_effect=Exception("Connection failed")),
    ):
        result = executor._read_bootstrap_log_sync()
        assert result is None


async def test_wmi_executor_enable_winrm_timeout_with_log():
    from executors.wmi_executor import WmiBootstrapError

    executor = WMIExecutor("127.0.0.1", "user", "pass")

    with (
        patch.object(executor, "execute", new_callable=AsyncMock) as mock_execute,
        patch.object(executor, "_wait_for_port", new_callable=AsyncMock) as mock_wait,
        patch.object(executor, "_read_bootstrap_log_sync", return_value="Some error in bootstrap") as mock_read_log,
    ):
        mock_wait.return_value = False

        try:
            await executor.enable_winrm()
            assert False, "Should have raised WmiBootstrapError"
        except WmiBootstrapError as e:
            assert "Не удалось дождаться открытия порта WinRM" in str(e)

        mock_execute.assert_called_once()
        mock_wait.assert_called_once_with(5985, timeout=120.0)
        mock_read_log.assert_called_once()


if __name__ == "__main__":
    asyncio.run(test_install())
