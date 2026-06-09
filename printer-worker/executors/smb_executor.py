import os
import logging
import asyncio
from typing import Tuple
from smbclient import register_session, copyfile, mkdir
from worker_config import WINRM_USERNAME, WINRM_PASSWORD

logger = logging.getLogger(__name__)

class SMBExecutor:
    """
    Класс для работы с SMB-ресурсами удаленных компьютеров (например, C$\\Windows\\Temp).
    Копирование файлов выполняется в потоке с использованием asyncio.to_thread.
    """
    def __init__(self):
        self.username = WINRM_USERNAME
        self.password = WINRM_PASSWORD

    def _copy_file_sync(self, src: str, dest_host: str, dest_path: str) -> bool:
        try:
            # Регистрируем сессию для удаленного хоста
            register_session(dest_host, username=self.username, password=self.password)
            
            # Назначение папки назначения (по умолчанию C$\\Windows\\Temp)
            # Формируем UNC-путь, например: \\\\pc-admin\\C$\\Windows\\Temp\\driver.inf
            unc_dest_dir = f"\\\\{dest_host}\\C$\\Windows\\Temp\\printer_drivers"
            
            try:
                mkdir(unc_dest_dir)
            except Exception:
                # Папка уже может существовать
                pass

            filename = os.path.basename(src)
            unc_dest_file = f"{unc_dest_dir}\\{filename}"

            logger.info("SMB -> Копирование %s в %s", src, unc_dest_file)
            copyfile(src, unc_dest_file)
            return True
        except Exception as e:
            logger.error("Ошибка копирования SMB файла %s на %s: %s", src, dest_host, e)
            return False

    async def copy_driver_to_temp(self, src: str, dest_host: str) -> bool:
        """
        Асинхронно копирует файл драйвера в C:\\Windows\\Temp\\printer_drivers на удаленный ПК.
        """
        return await asyncio.to_thread(self._copy_file_sync, src, dest_host, "C$\\Windows\\Temp")

smb_executor = SMBExecutor()
