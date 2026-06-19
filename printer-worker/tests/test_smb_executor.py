import os
import stat as stat_mod
import pytest
from unittest.mock import patch, MagicMock

# Устанавливаем переменные окружения до импорта
os.environ["BOT_API_KEY"] = "dummy_key"
os.environ["WINRM_USERNAME"] = "dummy_user"
os.environ["WINRM_PASSWORD"] = "dummy_pass"

from executors.smb_executor import SMBExecutor


def test_extract_smb_host():
    assert SMBExecutor._extract_smb_host("\\\\srv\\share\\path") == "srv"
    assert SMBExecutor._extract_smb_host("\\\\10.244.1.1\\C$\\Temp") == "10.244.1.1"


def test_parse_driver_path():
    src, dest, inf = SMBExecutor.parse_driver_path("\\\\srv\\share\\drv\\driver.inf")
    assert src == "\\\\srv\\share\\drv"
    assert dest == "drv"
    assert inf == "driver.inf"

    src2, dest2, inf2 = SMBExecutor.parse_driver_path("\\\\srv\\share\\drv_folder")
    assert src2 == "\\\\srv\\share\\drv_folder"
    assert dest2 == "drv_folder"
    assert inf2 == "*.inf"


def test_copy_dir_smb():
    executor = SMBExecutor()

    # Мокаем listdir, чтобы он вернул один файл и одну поддиректорию
    mock_listdir = MagicMock(
        side_effect=[
            ["file.txt", "subdir"],  # Вызов для корня
            ["subfile.txt"],  # Вызов для subdir
        ]
    )

    # Мокаем stat для определения типов файлов
    mock_root_file_stat = MagicMock()
    mock_root_file_stat.st_mode = stat_mod.S_IFREG
    mock_root_file_stat.st_size = 100

    mock_subdir_stat = MagicMock()
    mock_subdir_stat.st_mode = stat_mod.S_IFDIR

    mock_sub_file_stat = MagicMock()
    mock_sub_file_stat.st_mode = stat_mod.S_IFREG
    mock_sub_file_stat.st_size = 50

    mock_stat = MagicMock(
        side_effect=[
            mock_root_file_stat,  # stat(file.txt)
            mock_root_file_stat,  # stat(file.txt) после копирования (src)
            mock_root_file_stat,  # stat(file.txt) после копирования (dst)
            mock_subdir_stat,  # stat(subdir)
            mock_sub_file_stat,  # stat(subdir/subfile.txt)
            mock_sub_file_stat,  # stat(subdir/subfile.txt) после копирования (src)
            mock_sub_file_stat,  # stat(subdir/subfile.txt) после копирования (dst)
        ]
    )

    mock_open_file = MagicMock()
    # open_file() context manager mock
    mock_file_context = MagicMock()
    mock_file_context.read.side_effect = [b"data", b"", b"data", b""]
    mock_open_file.return_value.__enter__.return_value = mock_file_context

    with (
        patch("smbclient.listdir", mock_listdir),
        patch("executors.smb_executor.stat", mock_stat),
        patch("smbclient.open_file", mock_open_file),
        patch("smbclient.makedirs") as mock_makedirs,
    ):
        executor._copy_dir_smb("\\\\src\\share", "\\\\dst\\share")

        assert mock_makedirs.call_count == 2
        assert mock_listdir.call_count == 2
        assert mock_open_file.call_count == 4  # 2 чтения + 2 записи


@pytest.mark.asyncio
async def test_copy_driver_to_temp_success():
    executor = SMBExecutor()

    with (
        patch("executors.smb_executor.register_session") as mock_register,
        patch.object(executor, "_copy_dir_smb") as mock_copy_dir,
    ):
        res = await executor.copy_driver_to_temp(
            src="\\\\srv\\drivers\\model\\driver.inf", dest_host="target-pc"
        )
        assert res is True
        assert mock_register.call_count == 2
        mock_copy_dir.assert_called_once()


@pytest.mark.asyncio
async def test_copy_driver_to_temp_failure():
    executor = SMBExecutor()

    with (
        patch("executors.smb_executor.register_session", side_effect=Exception("Auth error")),
        patch.object(executor, "_copy_dir_smb", side_effect=Exception("Auth error")),
    ):
        res = await executor.copy_driver_to_temp(
            src="\\\\srv\\drivers\\model\\driver.inf", dest_host="target-pc"
        )
        assert res is False


@pytest.mark.asyncio
async def test_check_source_accessible_success():
    executor = SMBExecutor()

    with (
        patch("executors.smb_executor.register_session") as mock_register,
        patch("executors.smb_executor.stat") as mock_stat,
    ):
        res = await executor.check_source_accessible(
            "\\\\srv\\drivers\\model\\driver.inf"
        )
        assert res is True
        mock_register.assert_called_once()
        mock_stat.assert_called_once()


@pytest.mark.asyncio
async def test_check_source_accessible_failure():
    executor = SMBExecutor()

    with (
        patch("executors.smb_executor.register_session", side_effect=Exception("Host unreachable")),
        patch("executors.smb_executor.stat", side_effect=Exception("Stat failed")),
    ):
        res = await executor.check_source_accessible(
            "\\\\srv\\drivers\\model\\driver.inf"
        )
        assert res is False
