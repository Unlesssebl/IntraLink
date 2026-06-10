import logging
import asyncio
from typing import Tuple
import winrm
from worker_config import WINRM_USERNAME, WINRM_PASSWORD, WINRM_TRANSPORT

logger = logging.getLogger(__name__)

class WinRMExecutor:
    """
    Класс для выполнения удаленных команд PowerShell на Windows машинах.
    Все вызовы pywinrm обернуты в asyncio.to_thread для предотвращения блокировки event loop.
    """
    def __init__(self):
        self.username = WINRM_USERNAME
        self.password = WINRM_PASSWORD
        self.transport = WINRM_TRANSPORT

    def _run_ps_sync(self, target_pc: str, script: str) -> Tuple[int, str, str]:
        # pywinrm требует указания URL эндпоинта WinRM
        # По умолчанию используем HTTP на порту 5985
        endpoint = f"http://{target_pc}:5985/wsman"
        try:
            session = winrm.Session(
                endpoint,
                auth=(self.username, self.password),
                transport=self.transport,
                server_cert_validation='ignore',
                read_timeout_sec=70,
                operation_timeout_sec=60
            )
            # Запуск PowerShell скрипта
            rs = session.run_ps(script)
            std_out = rs.std_out.decode('utf-8', errors='ignore')
            std_err = rs.std_err.decode('utf-8', errors='ignore')
            return rs.status_code, std_out, std_err
        except Exception as e:
            logger.error("Сбой WinRM подключения к %s: %s", target_pc, e)
            return -1, "", str(e)

    async def run_powershell(self, target_pc: str, script: str, timeout: float = 90.0) -> Tuple[int, str, str]:
        """
        Асинхронный запуск PowerShell скрипта на целевом компьютере.
        """
        logger.info("WinRM [PowerShell] -> %s: запуск команды", target_pc)
        try:
            status_code, stdout, stderr = await asyncio.wait_for(
                asyncio.to_thread(self._run_ps_sync, target_pc, script),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error("Таймаут (%s сек) при выполнении PowerShell на хосте %s", timeout, target_pc)
            status_code, stdout, stderr = -1, "", f"Таймаут выполнения команды ({timeout} сек)"

        if status_code != 0:
            logger.warning("WinRM [PowerShell] -> %s завершился с кодом %d. Ошибка: %s", target_pc, status_code, stderr)
        return status_code, stdout, stderr

winrm_executor = WinRMExecutor()
