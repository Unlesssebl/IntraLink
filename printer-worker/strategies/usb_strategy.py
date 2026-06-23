import logging
import asyncio
from .base import PrinterStrategy, escape_ps
from . import strategy
from orchestrator.schemas import PrintJob, JobState, ConnectionType
from executors.winrm_executor import winrm_executor
from executors.smb_executor import smb_executor

logger = logging.getLogger(__name__)

# Параметры ожидания PnP-обнаружения принтера Windows
_PNP_POLL_INTERVAL = 2.0  # секунды между попытками проверки
_PNP_POLL_TIMEOUT = 30.0  # максимальное суммарное время ожидания


async def _wait_for_printer_pnp(target_pc: str, driver_name: str) -> bool:
    """
    Ожидает, пока Windows PnP не зарегистрирует принтер с указанным именем драйвера.
    Возвращает True, если принтер появился в течение _PNP_POLL_TIMEOUT секунд.
    Используется вместо слепого asyncio.sleep для надёжной верификации USB-принтеров.
    """
    driver_name_esc = escape_ps(driver_name)
    verify_script = (
        f"Get-CimInstance Win32_Printer "
        f"| Where-Object {{$_.DriverName -like '*{driver_name_esc}*'}} "
        f"| Select-Object Name, DriverName | ConvertTo-Json"
    )
    elapsed = 0.0
    while elapsed < _PNP_POLL_TIMEOUT:
        status, stdout, stderr = await winrm_executor.run_powershell(
            target_pc, verify_script
        )
        if status == 0 and stdout.strip():
            return True
        await asyncio.sleep(_PNP_POLL_INTERVAL)
        elapsed += _PNP_POLL_INTERVAL
    return False


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
        script = "Get-CimInstance Win32_PnPEntity -Filter \"PNPDeviceID like 'USBPRINT\\\\%'\" | Select-Object -ExpandProperty PNPDeviceID"
        status, stdout, stderr = await winrm_executor.run_powershell(
            job.target_pc, script
        )

        if status == -1:
            logger.warning(
                "Не удалось подключиться к ПК %s через WinRM: %s", job.target_pc, stderr or "timeout"
            )
            job.state = JobState.FAILED
            job.error_message = f"Не удалось инициализировать подключение WinRM к ПК {job.target_pc}: {stderr or 'Таймаут подключения'}"
            return job

        if status != 0 or not stdout.strip():
            logger.warning(
                "USB-принтеры не обнаружены на ПК %s (кабель отключен или принтер выключен)",
                job.target_pc,
            )
            job.state = JobState.WAITING
            job.error_message = (
                "Кабель USB принтера не подключен или устройство выключено"
            )
            return job

        pnp_id = stdout.strip().split("\n")[0].strip()
        logger.info(
            "Обнаружен USB-принтер с PNP ID: %s на ПК %s", pnp_id, job.target_pc
        )

        if not self.kb:
            from worker_main import get_kb

            self.kb = get_kb()

        driver = self.kb.find_by_hw_id(pnp_id)
        if not driver:
            logger.error(
                "Не найден подходящий драйвер для Hardware ID: %s в Базе Знаний", pnp_id
            )
            job.state = JobState.FAILED
            job.error_message = (
                f"Устройство с Hardware ID {pnp_id} не найдено в Базе Знаний"
            )
            return job

        job.model_key = driver.model_key
        job.driver_info = driver
        job.connection_type = ConnectionType.USB
        logger.info(
            "Hardware ID %s успешно сопоставлен с моделью %s (%s)",
            pnp_id,
            driver.model_key,
            driver.display_name,
        )
        return job

    async def execute(self, job: PrintJob) -> PrintJob:
        """
        Выполняет установку USB-принтера.
        Предполагает, что probe() уже был вызван оркестратором — job.driver_info установлен.
        Повторный вызов probe() здесь намеренно отсутствует.
        """
        from worker_services.redis_listener import save_job_state

        assert job.target_pc is not None
        assert job.driver_info is not None

        driver_name = job.driver_info.driver_name
        inf_path = job.driver_info.driver_inf_path

        if not inf_path or not inf_path.lower().endswith(".inf"):
            job.state = JobState.FAILED
            job.error_message = (
                f"Некорректный путь к драйверу: '{inf_path}'. "
                f"Путь должен указывать на конкретный файл .inf, а не на директорию."
            )
            await save_job_state(job)
            return job

        _, dest_subdir, inf_filename = smb_executor.parse_driver_path(inf_path)
        remote_temp_path = (
            f"C:\\Windows\\Temp\\printer_drivers\\{dest_subdir}\\{inf_filename}"
        )
        remote_temp_path_esc = escape_ps(remote_temp_path)

        # 1. Проверка доступности источника перед копированием
        if not await smb_executor.check_source_accessible(inf_path):
            job.state = JobState.FAILED
            job.error_message = f"Источник драйвера недоступен по SMB: {inf_path}"
            await save_job_state(job)
            return job

        # 2. Копируем драйвер на целевой ПК
        job.state = JobState.COPYING
        await save_job_state(job)
        logger.info("Копирование драйвера %s по SMB на ПК %s", inf_path, job.target_pc)
        success = await smb_executor.copy_driver_to_temp(inf_path, job.target_pc)
        if not success:
            job.state = JobState.FAILED
            job.error_message = (
                f"Не удалось скопировать драйвер на удаленный ПК {job.target_pc} по SMB"
            )
            await save_job_state(job)
            return job

        # 3. Установка драйвера через pnputil
        job.state = JobState.INSTALLING
        await save_job_state(job)
        logger.info(
            "Регистрация USB-драйвера в ОС через pnputil на ПК %s", job.target_pc
        )
        
        # Извлечение и импорт сертификата подписи драйвера в доверенные издатели (TrustedPublisher).
        # Это устраняет ошибку "The publisher of an Authenticode(tm) signed catalog has not yet been established as trusted."
        cat_path = remote_temp_path.replace(".inf", ".cat")
        cat_path_esc = escape_ps(cat_path)
        
        install_script = (
            f"$catPath = '{cat_path_esc}'; "
            f"if (Test-Path $catPath) {{ "
            f"  $sig = Get-AuthenticodeSignature $catPath; "
            f"  $cert = $sig.SignerCertificate; "
            f"  if ($cert) {{ "
            f"    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store('TrustedPublisher', 'LocalMachine'); "
            f"    $store.Open('ReadWrite'); "
            f"    $store.Add($cert); "
            f"    $store.Close(); "
            f"  }} "
            f"}}; "
            f"pnputil /add-driver '{remote_temp_path_esc}' /install"
        )
        status, stdout, stderr = await winrm_executor.run_powershell(
            job.target_pc, install_script
        )
        if status != 0:
            job.state = JobState.FAILED
            job.error_message = f"Ошибка выполнения pnputil: {stderr or stdout}"
            await save_job_state(job)
            return job

        # 4. Верификация через polling вместо слепого sleep.
        # pnputil установил INF, теперь ждём автоматического PnP-обнаружения принтера.
        job.state = JobState.VERIFYING
        await save_job_state(job)
        logger.info(
            "Ожидание PnP-обнаружения принтера на ПК %s (до %.0f сек)...",
            job.target_pc,
            _PNP_POLL_TIMEOUT,
        )
        found = await _wait_for_printer_pnp(job.target_pc, driver_name)

        if not found:
            # PnP не справился сам — принудительный рескан устройств.
            # pnputil /scan-devices — правильный способ инициировать рескан на Windows 10+.
            logger.info(
                "PnP не обнаружил принтер автоматически, запускаем принудительный рескан на ПК %s",
                job.target_pc,
            )
            rescan_script = "pnputil /scan-devices"
            await winrm_executor.run_powershell(job.target_pc, rescan_script)

            # Повторное ожидание после рескана
            found = await _wait_for_printer_pnp(job.target_pc, driver_name)

        if found:
            logger.info(
                "USB-принтер успешно определился в системе на %s", job.target_pc
            )
            job.state = JobState.DONE
            job.error_message = None
            await save_job_state(job)
        else:
            job.state = JobState.FAILED
            job.error_message = (
                "Драйвер установлен, но Windows PnP не обнаружил принтер. "
                "Проверьте USB-подключение."
            )
            await save_job_state(job)

        return job
