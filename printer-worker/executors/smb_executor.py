import logging
import asyncio
import ntpath
import stat as stat_mod
import urllib.parse
from typing import Tuple
from smbclient import register_session, mkdir, stat
from worker_config import WINRM_USERNAME, WINRM_PASSWORD

logger = logging.getLogger(__name__)


class SMBExecutor:
    """
    Класс для работы с SMB-ресурсами удаленных компьютеров (например, C$\\Windows\\Temp).
    Копирование файлов выполняется в потоке с использованием asyncio.to_thread.
    """

    def __init__(self, username: str | None = None, password: str | None = None):
        self.username = username or WINRM_USERNAME
        self.password = password or WINRM_PASSWORD

    @staticmethod
    def _extract_smb_host(unc_path: str) -> str:
        parsed = urllib.parse.urlsplit("smb:" + unc_path.replace("\\", "/"))
        return parsed.netloc or unc_path.strip("\\").split("\\")[0]

    def _copy_dir_smb(self, src_dir: str, dest_dir: str):
        from smbclient import listdir, open_file

        try:
            mkdir(dest_dir)
        except Exception as e:
            err_str = str(e)
            if "0xC0000035" not in err_str and "object_name_collision" not in err_str.lower():
                logger.error("SMB: не удалось создать директорию %s: %s", dest_dir, e)
                raise

        for item in listdir(src_dir):
            src_item = f"{src_dir}\\{item}"
            dest_item = f"{dest_dir}\\{item}"
            try:
                # Если это директория, stat отработает, если файл - тоже, но в smbclient нет isdir,
                # можно использовать S_ISDIR(stat(src_item).st_mode)
                mode = stat(src_item).st_mode
                if stat_mod.S_ISDIR(mode):
                    self._copy_dir_smb(src_item, dest_item)
                else:
                    with open_file(src_item, mode="rb") as f_src:
                        with open_file(dest_item, mode="wb") as f_dst:
                            while chunk := f_src.read(4 * 1024 * 1024):
                                f_dst.write(chunk)
                    
                    src_size = stat(src_item).st_size
                    dst_size = stat(dest_item).st_size
                    if src_size != dst_size:
                        logger.error("SMB: размер файла не совпадает после копирования %s -> %s (%s != %s)", src_item, dest_item, src_size, dst_size)
                        raise Exception(f"File size mismatch: {src_size} != {dst_size}")
            except Exception as e:
                logger.error(f"Ошибка копирования элемента {src_item}: {e}")
                raise

    @staticmethod
    def parse_driver_path(inf_path: str) -> Tuple[str, str, str]:
        """
        Разбирает Windows-путь к драйверу (который может быть файлом .inf или папкой).
        Возвращает кортеж: (src_dir, dest_subdir, inf_filename)
        """
        normalized_path = inf_path.rstrip("\\")
        if normalized_path.lower().endswith(".inf"):
            src_dir = ntpath.dirname(normalized_path)
            dest_subdir = ntpath.basename(src_dir)
            inf_filename = ntpath.basename(normalized_path)
        else:
            src_dir = normalized_path
            dest_subdir = ntpath.basename(src_dir)
            inf_filename = "*.inf"
        return src_dir, dest_subdir, inf_filename

    def _copy_file_sync(self, src: str, dest_host: str, dest_path: str) -> bool:
        try:
            # Разбираем путь с помощью статического метода
            src_dir, dest_subdir, _ = self.parse_driver_path(src)

            src_host = self._extract_smb_host(src_dir)
            try:
                register_session(
                    src_host, username=self.username, password=self.password
                )
            except Exception as e:
                logger.debug("SMB: регистрация сессии для %s не удалась (возможно, уже зарегистрирована): %s", src_host, e)

            try:
                register_session(
                    dest_host, username=self.username, password=self.password
                )
            except Exception as e:
                logger.debug("SMB: регистрация сессии для %s не удалась (возможно, уже зарегистрирована): %s", dest_host, e)

            unc_dest_dir = (
                f"\\\\{dest_host}\\{dest_path}\\printer_drivers\\{dest_subdir}"
            )

            logger.info("SMB -> Копирование директории %s в %s", src_dir, unc_dest_dir)
            self._copy_dir_smb(src_dir, unc_dest_dir)

            return True
        except Exception as e:
            logger.error(
                "Ошибка копирования SMB директории %s на %s: %s", src, dest_host, e
            )
            return False

    async def copy_driver_to_temp(
        self, src: str, dest_host: str, timeout: float = 600.0
    ) -> bool:
        """
        Асинхронно копирует файл драйвера в C:\\Windows\\Temp\\printer_drivers на удаленный ПК.
        """
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._copy_file_sync, src, dest_host, "C$\\Windows\\Temp"
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error(
                "Таймаут (%s сек) при SMB копировании драйвера %s на хост %s",
                timeout,
                src,
                dest_host,
            )
            return False


    async def check_source_accessible(self, src: str) -> bool:
        """
        Асинхронная проверка доступности UNC-пути источника перед копированием.
        Сетевой вызов stat() выполняется в потоке, чтобы не блокировать event loop.
        """
        def _check_sync() -> bool:
            try:
                src_dir, _, _ = self.parse_driver_path(src)
                src_host = self._extract_smb_host(src_dir)
                try:
                    register_session(src_host, username=self.username, password=self.password)
                except Exception as e:
                    logger.debug(
                        "SMB: регистрация сессии для %s не удалась (возможно, уже зарегистрирована): %s",
                        src_host, e,
                    )
                stat(src_dir)
                return True
            except Exception as e:
                logger.error("SMB: источник '%s' недоступен: %s", src, e)
                return False

        return await asyncio.to_thread(_check_sync)


smb_executor = SMBExecutor()

