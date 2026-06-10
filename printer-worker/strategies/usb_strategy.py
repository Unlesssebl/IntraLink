import os
import logging
import asyncio
from .base import PrinterStrategy
from . import strategy
from orchestrator.schemas import PrintJob, JobState, ConnectionType, KnowledgeBase
from executors.winrm_executor import winrm_executor
from executors.smb_executor import smb_executor

logger = logging.getLogger(__name__)

def escape_ps(text: str) -> str:
    """
    Экранирует одинарные кавычки для безопасной подстановки параметров в PowerShell скрипты.
    """
    return text.replace("'", "''")

@strategy(ConnectionType.USB)
class UsbDiscoveryStrategy(PrinterStrategy):
    def __init__(self, kb=None):
        self.kb = kb

    async def probe(self, job: PrintJob) -> PrintJob:
        if not job.target_pc:
            job.state = JobState.FAILED
            job.error_message = "Целевой компьютер не указан"
            return job

        logger.info("Запуск WinRM PnP поиска USB-принтеров на ПК %s", job.target_pc)
        script = 'Get-CimInstance Win32_PnPEntity -Filter "PNPDeviceID like \'USBPRINT\\\\%\'" | Select-Object -ExpandProperty PNPDeviceID'
        status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, script)

        if status != 0 or not stdout.strip():
            logger.warning("USB-принтеры не обнаружены на ПК %s (кабель отключен или принтер выключен)", job.target_pc)
            job.state = JobState.WAITING
            job.error_message = "Кабель USB принтера не подключен или устройство выключено"
            return job

        pnp_id = stdout.strip().split("\n")[0].strip()
        logger.info("Обнаружен USB-принтер с PNP ID: %s на ПК %s", pnp_id, job.target_pc)

        # Сопоставляем с Базой Знаний
        # Если self.kb не передан напрямую, мы его загрузим или получим из реестра/контекста
        if not self.kb:
            # Импортируем локально во избежание круговых импортов
            from worker_main import get_kb
            self.kb = get_kb()

        driver = self.kb.find_by_hw_id(pnp_id)
        if not driver:
            logger.error("Не найден подходящий драйвер для Hardware ID: %s в Базе Знаний", pnp_id)
            job.state = JobState.FAILED
            job.error_message = f"Устройство с Hardware ID {pnp_id} не найдено в Базе Знаний"
            return job

        job.model_key = driver.model_key
        job.driver_info = driver
        job.connection_type = ConnectionType.USB
        logger.info("Hardware ID %s успешно сопоставлен с моделью %s (%s)", pnp_id, driver.model_key, driver.display_name)
        return job

    async def execute(self, job: PrintJob) -> PrintJob:
        from worker_services.redis_listener import save_job_state
        # 1. Diagnostics
        job = await self.probe(job)
        if job.state in (JobState.WAITING, JobState.FAILED):
            await save_job_state(job)
            return job

        assert job.target_pc is not None
        assert job.driver_info is not None

        driver_name = job.driver_info.driver_name
        driver_name_esc = escape_ps(driver_name)
        inf_path = job.driver_info.driver_inf_path
        
        _, dest_subdir, inf_filename = smb_executor.parse_driver_path(inf_path)
        remote_temp_path = f"C:\\Windows\\Temp\\printer_drivers\\{dest_subdir}\\{inf_filename}"
        remote_temp_path_esc = escape_ps(remote_temp_path)

        # 2. Копируем драйвер на целевой ПК
        job.state = JobState.COPYING
        await save_job_state(job)
        logger.info("Копирование драйвера %s по SMB на ПК %s", inf_path, job.target_pc)
        success = await smb_executor.copy_driver_to_temp(inf_path, job.target_pc)
        if not success:
            job.state = JobState.FAILED
            job.error_message = f"Не удалось скопировать драйвер на удаленный ПК {job.target_pc} по SMB"
            await save_job_state(job)
            return job

        # 3. Установка драйвера через pnputil
        job.state = JobState.INSTALLING
        await save_job_state(job)
        logger.info("Регистрация USB-драйвера в ОС через pnputil на ПК %s", job.target_pc)
        install_script = f"pnputil /add-driver '{remote_temp_path_esc}' /install"
        status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, install_script)
        if status != 0:
            job.state = JobState.FAILED
            job.error_message = f"Ошибка выполнения pnputil: {stderr or stdout}"
            await save_job_state(job)
            return job

        # Даем Windows Plug-and-Play время на автоматическое обнаружение и создание принтера
        logger.info("Ожидание Plug-and-Play автоопределения принтера (5 секунд)...")
        await asyncio.sleep(5)

        # 4. Верификация
        job.state = JobState.VERIFYING
        await save_job_state(job)
        # Проверяем наличие принтера с данным именем драйвера
        verify_script = f"Get-CimInstance Win32_Printer | Where-Object {{$_.DriverName -like '*{driver_name_esc}*'}} | Select-Object Name, DriverName | ConvertTo-Json"
        status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, verify_script)

        if status == 0 and stdout.strip():
            logger.info("USB-принтер успешно определился в системе на %s: %s", job.target_pc, stdout.strip())
            job.state = JobState.DONE
            job.error_message = None
            await save_job_state(job)
        else:
            # Попробуем сделать принудительный рескан PnP
            rescan_script = (
                "Get-CimInstance Win32_PnPEntity | Where-Object {$_.PNPDeviceID -like 'USBPRINT*'} | ForEach-Object { "
                "  $_.InvokeMethod('Enable', $null) "
                "}"
            )
            await winrm_executor.run_powershell(job.target_pc, rescan_script)
            await asyncio.sleep(3)
            
            # Повторная верификация
            status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, verify_script)
            if status == 0 and stdout.strip():
                logger.info("USB-принтер успешно определился после рескана PnP на %s", job.target_pc)
                job.state = JobState.DONE
                job.error_message = None
                await save_job_state(job)
            else:
                job.state = JobState.FAILED
                job.error_message = "Драйвер установлен, но Windows PnP не обнаружил принтер. Проверьте USB-подключение."
                await save_job_state(job)

        return job
