import logging
from .base import PrinterStrategy, escape_ps
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
        driver_name_esc = escape_ps(driver_name)
        logger.info(
            "Проверка наличия драйвера '%s' на ПК %s", driver_name, job.target_pc
        )

        script = (
            f"Get-PrinterDriver -Name '{driver_name_esc}' -ErrorAction SilentlyContinue"
        )
        status, stdout, stderr = await winrm_executor.run_powershell(
            job.target_pc, script
        )

        if status == 0 and driver_name in stdout:
            logger.info(
                "Драйвер '%s' уже установлен на ПК %s", driver_name, job.target_pc
            )
            job.driver_installed = True
        else:
            logger.info("Драйвер '%s' отсутствует на ПК %s", driver_name, job.target_pc)
            job.driver_installed = False

        return job

    async def execute(self, job: PrintJob) -> PrintJob:
        """
        Выполняет установку TCP/IP-принтера.
        Предполагает, что probe() уже был вызван оркестратором — job.driver_installed установлен.
        Повторный вызов probe() здесь намеренно отсутствует.

        Имя устанавливаемого принтера формируется по шаблону: "{display_name} ({suffix})",
        где suffix — это 3-й и 4-й октеты IP-адреса (если адрес в формате IPv4) или полный hostname МФУ.
        """
        from worker_services.redis_listener import save_job_state

        if not job.target_pc or not job.driver_info or not job.printer_address:
            job.state = JobState.FAILED
            job.error_message = (
                "Неполные параметры сетевого принтера (IP/DNS адрес не указан)"
            )
            await save_job_state(job)
            return job

        assert job.target_pc is not None
        assert job.driver_info is not None
        assert job.printer_address is not None

        driver_name = job.driver_info.driver_name
        driver_name_esc = escape_ps(driver_name)
        inf_path = job.driver_info.driver_inf_path

        _, dest_subdir, inf_filename = smb_executor.parse_driver_path(inf_path)
        remote_temp_path = (
            f"C:\\Windows\\Temp\\printer_drivers\\{dest_subdir}\\{inf_filename}"
        )
        remote_temp_path_esc = escape_ps(remote_temp_path)

        # 1. Копирование и установка драйвера при необходимости.
        # Если probe() определил, что драйвер отсутствует (driver_installed=False) или
        # probe() не был вызван (driver_installed=None) — копируем на всякий случай.
        if not job.driver_installed:
            job.state = JobState.COPYING
            await save_job_state(job)
            logger.info("Копирование драйвера %s на %s", inf_path, job.target_pc)

            # Проверка доступности источника перед копированием
            if not await smb_executor.check_source_accessible(inf_path):
                job.state = JobState.FAILED
                job.error_message = f"Источник драйвера недоступен по SMB: {inf_path}"
                await save_job_state(job)
                return job

            success = await smb_executor.copy_driver_to_temp(inf_path, job.target_pc)
            if not success:
                job.state = JobState.FAILED
                job.error_message = f"Не удалось скопировать драйвер на удаленный ПК {job.target_pc} по SMB"
                await save_job_state(job)
                return job

            job.state = JobState.INSTALLING
            await save_job_state(job)
            logger.info("Установка драйвера '%s' на ПК %s", driver_name, job.target_pc)
            install_driver_script = (
                f"pnputil /add-driver '{remote_temp_path_esc}' /install ; "
                f"Add-PrinterDriver -Name '{driver_name_esc}'"
            )
            status, stdout, stderr = await winrm_executor.run_powershell(
                job.target_pc, install_driver_script
            )
            if status != 0:
                job.state = JobState.FAILED
                job.error_message = (
                    f"Сбой добавления драйвера принтера в ОС: {stderr or stdout}"
                )
                await save_job_state(job)
                return job

        # 2. Создание TCP/IP порта
        job.state = JobState.INSTALLING
        await save_job_state(job)
        port_name = f"IP_{job.printer_address}"
        port_name_esc = escape_ps(port_name)
        printer_address_esc = escape_ps(job.printer_address)
        logger.info("Создание сетевого порта %s на ПК %s", port_name, job.target_pc)
        port_script = (
            f"if (-not (Get-PrinterPort -Name '{port_name_esc}' -ErrorAction SilentlyContinue)) {{"
            f"  Add-PrinterPort -Name '{port_name_esc}' -PrinterHostAddress '{printer_address_esc}'"
            f"}}"
        )
        status, stdout, stderr = await winrm_executor.run_powershell(
            job.target_pc, port_script
        )
        if status != 0:
            job.state = JobState.FAILED
            job.error_message = (
                f"Сбой создания сетевого порта {port_name}: {stderr or stdout}"
            )
            await save_job_state(job)
            return job

        # 3. Добавление или обновление принтера
        import re
        address = job.printer_address
        if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", address):
            suffix = ".".join(address.split(".")[2:])
        else:
            suffix = address

        printer_name = f"{job.driver_info.display_name} ({suffix})"
        printer_name_esc = escape_ps(printer_name)
        logger.info(
            "Создание или обновление свойств принтера '%s' на ПК %s",
            printer_name,
            job.target_pc,
        )
        printer_script = (
            f"if (-not (Get-Printer -Name '{printer_name_esc}' -ErrorAction SilentlyContinue)) {{"
            f"  Add-Printer -Name '{printer_name_esc}' -DriverName '{driver_name_esc}' -PortName '{port_name_esc}'"
            f"}} else {{"
            f"  Set-Printer -Name '{printer_name_esc}' -DriverName '{driver_name_esc}' -PortName '{port_name_esc}'"
            f"}}"
        )
        status, stdout, stderr = await winrm_executor.run_powershell(
            job.target_pc, printer_script
        )
        if status != 0:
            job.state = JobState.FAILED
            job.error_message = (
                f"Сбой добавления/настройки принтера {printer_name}: {stderr or stdout}"
            )
            await save_job_state(job)
            return job

        # 4. Верификация
        job.state = JobState.VERIFYING
        await save_job_state(job)
        verify_script = f"Get-Printer -Name '{printer_name_esc}' | Select-Object Name, PortName, DriverName | ConvertTo-Json"
        status, stdout, stderr = await winrm_executor.run_powershell(
            job.target_pc, verify_script
        )

        if status == 0 and printer_name in stdout:
            logger.info(
                "Принтер '%s' успешно верифицирован на %s", printer_name, job.target_pc
            )
            job.state = JobState.DONE
            job.error_message = None
            await save_job_state(job)
        else:
            job.state = JobState.FAILED
            job.error_message = f"Верификация принтера не удалась: {stderr or stdout}"
            await save_job_state(job)

        return job
