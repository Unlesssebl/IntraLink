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

    def _copy_dir_smb(self, src_dir: str, dest_dir: str):
        from smbclient import listdir, mkdir, stat, open_file
        import posixpath
        try:
            mkdir(dest_dir)
        except Exception:
            pass
            
        for item in listdir(src_dir):
            src_item = f"{src_dir}\\{item}"
            dest_item = f"{dest_dir}\\{item}"
            try:
                # Если это директория, stat отработает, если файл - тоже, но в smbclient нет isdir,
                # можно использовать S_ISDIR(stat(src_item).st_mode)
                import stat as stat_mod
                mode = stat(src_item).st_mode
                if stat_mod.S_ISDIR(mode):
                    self._copy_dir_smb(src_item, dest_item)
                else:
                    with open_file(src_item, mode='rb') as f_src:
                        with open_file(dest_item, mode='wb') as f_dst:
                            f_dst.write(f_src.read())
            except Exception as e:
                logger.error(f"Ошибка копирования элемента {src_item}: {e}")
                raise

    def _copy_file_sync(self, src: str, dest_host: str, dest_path: str) -> bool:
        try:
            # Нам нужно также зарегистрировать сессию для исходного сервера, если он запаролен,
            # но в данном случае считаем, что доступ открыт или используется тот же юзер
            import urllib.parse
            src_host = src.strip("\\").split("\\")[0]
            try:
                register_session(src_host, username=self.username, password=self.password)
            except Exception:
                pass
                
            try:
                register_session(dest_host, username=self.username, password=self.password)
            except Exception:
                pass
            
            basename = os.path.basename(src.rstrip("\\"))
            unc_dest_dir = f"\\\\{dest_host}\\C$\\Windows\\Temp\\printer_drivers\\{basename}"
            
            logger.info("SMB -> Копирование директории %s в %s", src, unc_dest_dir)
            self._copy_dir_smb(src, unc_dest_dir)
            
            return True
        except Exception as e:
            logger.error("Ошибка копирования SMB директории %s на %s: %s", src, dest_host, e)
            return False

    async def copy_driver_to_temp(self, src: str, dest_host: str) -> bool:
        """
        Асинхронно копирует файл драйвера в C:\\Windows\\Temp\\printer_drivers на удаленный ПК.
        """
        return await asyncio.to_thread(self._copy_file_sync, src, dest_host, "C$\\Windows\\Temp")

smb_executor = SMBExecutor()
