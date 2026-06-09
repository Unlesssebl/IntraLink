import os
import logging
from .base import PrinterStrategy
from . import strategy
from orchestrator.schemas import PrintJob, JobState, ConnectionType
from executors.winrm_executor import winrm_executor
from executors.smb_executor import smb_executor

logger = logging.getLogger(__name__)

@strategy(ConnectionType.TCPIP)
class TcpIpPortStrategy(PrinterStrategy):
    async def probe(self, job: PrintJob) -> PrintJob:
        if not job.target_pc or not job.driver_info:
            job.state = JobState.FAILED
            job.error_message = "Недостаточно данных для диагностики (целевой ПК или информация о драйвере отсутствуют)"
            return job

        driver_name = job.driver_info.driver_name
        logger.info("Проверка наличия драйвера '%s' на ПК %s", driver_name, job.target_pc)
        
        # Скрипт проверки существования драйвера
        script = f"Get-PrinterDriver -Name '{driver_name}' -ErrorAction SilentlyContinue"
        status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, script)
        
        if status == 0 and driver_name in stdout:
            logger.info("Драйвер '%s' уже установлен на ПК %s", driver_name, job.target_pc)
            # Флаг о том, что копирование не требуется, сохраняем в метаданных/контексте
            job.error_message = "DRIVER_EXISTS"
        else:
            logger.info("Драйвер '%s' отсутствует на ПК %s", driver_name, job.target_pc)
            job.error_message = "DRIVER_MISSING"
            
        return job

    async def execute(self, job: PrintJob) -> PrintJob:
        if not job.target_pc or not job.driver_info or not job.printer_address:
            job.state = JobState.FAILED
            job.error_message = "Неполные параметры сетевого принтера (IP/DNS адрес не указан)"
            return job

        # 1. Диагностика
        job = await self.probe(job)
        if job.state == JobState.FAILED:
            return job

        driver_name = job.driver_info.driver_name
        inf_path = job.driver_info.driver_inf_path
        filename = os.path.basename(inf_path)
        remote_temp_path = f"C:\\Windows\\Temp\\printer_drivers\\{filename}"

        # 2. Копирование и установка драйвера при необходимости
        if job.error_message == "DRIVER_MISSING":
            job.state = JobState.COPYING
            logger.info("Копирование драйвера %s на %s", inf_path, job.target_pc)
            success = await smb_executor.copy_driver_to_temp(inf_path, job.target_pc)
            if not success:
                job.state = JobState.FAILED
                job.error_message = f"Не удалось скопировать драйвер на удаленный ПК {job.target_pc} по SMB"
                return job

            job.state = JobState.INSTALLING
            logger.info("Установка драйвера '%s' на ПК %s", driver_name, job.target_pc)
            # Скрипт импорта драйвера в Windows
            install_driver_script = (
                f"pnputil /add-driver '{remote_temp_path}' /install ; "
                f"Add-PrinterDriver -Name '{driver_name}'"
            )
            status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, install_driver_script)
            if status != 0:
                job.state = JobState.FAILED
                job.error_message = f"Сбой добавления драйвера принтера в ОС: {stderr or stdout}"
                return job

        # 3. Создание TCP/IP порта
        job.state = JobState.INSTALLING
        port_name = f"IP_{job.printer_address}"
        logger.info("Создание сетевого порта %s на ПК %s", port_name, job.target_pc)
        port_script = (
            f"if (-not (Get-PrinterPort -Name '{port_name}' -ErrorAction SilentlyContinue)) {{"
            f"  Add-PrinterPort -Name '{port_name}' -PrinterHostAddress '{job.printer_address}'"
            f"}}"
        )
        status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, port_script)
        if status != 0:
            job.state = JobState.FAILED
            job.error_message = f"Сбой создания сетевого порта {port_name}: {stderr or stdout}"
            return job

        # 4. Добавление принтера
        printer_name = job.driver_info.display_name
        logger.info("Создание принтера '%s' на ПК %s", printer_name, job.target_pc)
        printer_script = (
            f"if (-not (Get-Printer -Name '{printer_name}' -ErrorAction SilentlyContinue)) {{"
            f"  Add-Printer -Name '{printer_name}' -DriverName '{driver_name}' -PortName '{port_name}'"
            f"}}"
        )
        status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, printer_script)
        if status != 0:
            job.state = JobState.FAILED
            job.error_message = f"Сбой создания принтера {printer_name}: {stderr or stdout}"
            return job

        # 5. Верификация
        job.state = JobState.VERIFYING
        verify_script = f"Get-Printer -Name '{printer_name}' | Select-Object Name, PortName, DriverName | ConvertTo-Json"
        status, stdout, stderr = await winrm_executor.run_powershell(job.target_pc, verify_script)
        
        if status == 0 and printer_name in stdout:
            logger.info("Принтер '%s' успешно верифицирован на %s", printer_name, job.target_pc)
            job.state = JobState.DONE
            job.error_message = None
        else:
            job.state = JobState.FAILED
            job.error_message = f"Верификация принтера не удалась: {stderr or stdout}"

        return job
