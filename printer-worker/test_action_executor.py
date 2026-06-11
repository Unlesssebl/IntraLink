import pytest
from unittest.mock import patch, AsyncMock
from orchestrator.schemas import PrintJob, JobState, ConnectionType, PrinterDriverInfo
from worker_services.action_executor import execute_action

@pytest.mark.asyncio
async def test_execute_action_success():
    job = PrintJob(
        task_id=100,
        tg_user_id=555,
        raw_text="Установите принтер",
        state=JobState.DONE,
        target_pc="PC-TEST",
        connection_type=ConnectionType.TCPIP,
        driver_info=PrinterDriverInfo(
            model_key="kyocera_m2040dn",
            display_name="Kyocera ECOSYS M2040dn",
            driver_name="Kyocera ECOSYS M2040dn KX",
            driver_inf_path="\\\\srv\\share\\m2040.inf",
            vendor="Kyocera",
            connection_type=ConnectionType.TCPIP
        )
    )

    with (
        patch("worker_services.action_executor.update_task_status", new_callable=AsyncMock) as mock_update_status,
        patch("worker_services.action_executor.add_task_comment", new_callable=AsyncMock) as mock_add_comment
    ):
        await execute_action("on_success", job)
        
        mock_update_status.assert_called_once_with(555, 100, 29)  # STATUS_RESOLVED = 29
        mock_add_comment.assert_called_once()
        comment = mock_add_comment.call_args[0][2]
        assert "успешно установлен" in comment
        assert "PC-TEST" in comment
        assert "Kyocera ECOSYS M2040dn" in comment

@pytest.mark.asyncio
async def test_execute_action_usb_disconnected():
    job = PrintJob(
        task_id=101,
        tg_user_id=555,
        raw_text="Установите принтер по USB",
        state=JobState.WAITING,
        target_pc="PC-TEST",
        connection_type=ConnectionType.USB,
    )

    with (
        patch("worker_services.action_executor.update_task_status", new_callable=AsyncMock) as mock_update_status,
        patch("worker_services.action_executor.add_task_comment", new_callable=AsyncMock) as mock_add_comment
    ):
        await execute_action("on_usb_disconnected", job)
        
        mock_update_status.assert_called_once_with(555, 101, 35)  # STATUS_WAITING = 35
        mock_add_comment.assert_called_once()
        comment = mock_add_comment.call_args[0][2]
        assert "Не вижу МФУ." in comment
        assert "Переподключите usb кабель" in comment

@pytest.mark.asyncio
async def test_execute_action_error_pc_offline():
    job = PrintJob(
        task_id=102,
        tg_user_id=555,
        raw_text="Сетевой принтер",
        state=JobState.FAILED,
        target_pc="PC-OFFLINE",
        connection_type=ConnectionType.TCPIP,
    )

    with (
        patch("worker_services.action_executor.update_task_status", new_callable=AsyncMock) as mock_update_status,
        patch("worker_services.action_executor.add_task_comment", new_callable=AsyncMock) as mock_add_comment
    ):
        error_msg = "Не удалось инициализировать подключение (WMI Bootstrap): RPC server is unavailable"
        await execute_action("on_error", job, error_msg)
        
        mock_update_status.assert_called_once_with(555, 102, 35)  # STATUS_WAITING = 35
        mock_add_comment.assert_called_once()
        comment = mock_add_comment.call_args[0][2]
        assert "Не вижу ПК (PC-OFFLINE) в сети." in comment
        assert "1. Убедитесь в корректности имени ПК" in comment
        assert "специалист" not in comment.lower()

@pytest.mark.asyncio
async def test_execute_action_error_mfp_offline():
    job = PrintJob(
        task_id=103,
        tg_user_id=555,
        raw_text="Сетевой принтер",
        state=JobState.FAILED,
        target_pc="PC-TEST",
        connection_type=ConnectionType.TCPIP,
    )

    with (
        patch("worker_services.action_executor.update_task_status", new_callable=AsyncMock) as mock_update_status,
        patch("worker_services.action_executor.add_task_comment", new_callable=AsyncMock) as mock_add_comment
    ):
        error_msg = "ping failed for printer 10.244.12.34"
        await execute_action("on_error", job, error_msg)
        
        mock_update_status.assert_called_once_with(555, 103, 35)
        mock_add_comment.assert_called_once()
        comment = mock_add_comment.call_args[0][2]
        assert "Не вижу МФУ в сети." in comment
        assert "3. Переподключите сетевой кабель к МФУ;" in comment
        assert "специалист" not in comment.lower()

@pytest.mark.asyncio
async def test_execute_action_error_missing_data():
    job = PrintJob(
        task_id=104,
        tg_user_id=555,
        raw_text="Установите принтер",
        state=JobState.FAILED,
        connection_type=ConnectionType.TCPIP,
    )

    with (
        patch("worker_services.action_executor.update_task_status", new_callable=AsyncMock) as mock_update_status,
        patch("worker_services.action_executor.add_task_comment", new_callable=AsyncMock) as mock_add_comment
    ):
        error_msg = "Недостаточно данных для диагностики (целевой ПК или информация о драйвере отсутствуют)"
        await execute_action("on_error", job, error_msg)
        
        mock_update_status.assert_called_once_with(555, 104, 35)
        mock_add_comment.assert_called_once()
        comment = mock_add_comment.call_args[0][2]
        assert "Если принтер сетевой, укажите IP адрес" in comment
        assert "В случае подключения по USB укажите номер ПК" in comment
        assert "специалист" not in comment.lower()

@pytest.mark.asyncio
async def test_execute_action_error_default():
    job = PrintJob(
        task_id=105,
        tg_user_id=555,
        raw_text="Установите принтер",
        state=JobState.FAILED,
        connection_type=ConnectionType.TCPIP,
    )

    with (
        patch("worker_services.action_executor.update_task_status", new_callable=AsyncMock) as mock_update_status,
        patch("worker_services.action_executor.add_task_comment", new_callable=AsyncMock) as mock_add_comment
    ):
        error_msg = "Some random driver registration error in OS"
        await execute_action("on_error", job, error_msg)
        
        mock_update_status.assert_not_called()
        mock_add_comment.assert_not_called()


@pytest.mark.asyncio
async def test_execute_action_error_preformatted():
    job = PrintJob(
        task_id=106,
        tg_user_id=555,
        raw_text="Установите принтер",
        state=JobState.FAILED,
        connection_type=ConnectionType.TCPIP,
    )

    with (
        patch("worker_services.action_executor.update_task_status", new_callable=AsyncMock) as mock_update_status,
        patch("worker_services.action_executor.add_task_comment", new_callable=AsyncMock) as mock_add_comment
    ):
        error_msg = "Здравствуйте! Не вижу ваш компьютер в сети (PC-TEST) и принтер (10.244.1.1)."
        await execute_action("on_error", job, error_msg)
        
        mock_update_status.assert_called_once_with(555, 106, 35)
        mock_add_comment.assert_called_once_with(555, 106, error_msg)
