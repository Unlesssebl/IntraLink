import asyncio
import base64
import logging
from impacket.dcerpc.v5.dcomrt import DCOMConnection
from impacket.dcerpc.v5.dcom import wmi
from impacket.dcerpc.v5.dtypes import NULL

logger = logging.getLogger(__name__)


class WmiBootstrapError(Exception):
    pass


class WMIExecutor:
    """
    Класс для выполнения команд на удаленном сервере через WMI (DCOM/RPC).
    Используется исключительно для активации/деактивации WinRM,
    когда нет возможности настроить его через GPO.
    Все вызовы заворачиваются в asyncio.to_thread, чтобы не блокировать event loop.
    """

    def __init__(self, target_ip: str, username: str, password: str, domain: str = ""):
        self.target_ip = target_ip
        self.username = username
        self.password = password
        self.domain = domain

    def _sync_execute(self, command: str) -> int:
        """
        Синхронное выполнение команды через Win32_Process.Create.
        Возвращает код возврата метода Create (0 - успех).
        """
        logger.debug(f"Подключение к WMI на {self.target_ip} для выполнения: {command}")

        # Подключаемся к DCOM
        try:
            dcom = DCOMConnection(
                self.target_ip,
                self.username,
                self.password,
                self.domain,
                "",
                "",
                None,
                oxidResolver=True,
            )
        except Exception as e:
            logger.error(f"Ошибка подключения DCOM к {self.target_ip}: {e}")
            raise WmiBootstrapError(f"Ошибка DCOM: {e}")

        try:
            # Получаем интерфейс IWbemLevel1Login
            iInterface = dcom.CoCreateInstanceEx(
                wmi.CLSID_WbemLevel1Login, wmi.IID_IWbemLevel1Login
            )
            iWbemLevel1Login = wmi.IWbemLevel1Login(iInterface)

            # Логинимся в пространство имен root\cimv2
            iWbemServices = iWbemLevel1Login.NTLMLogin("//./root/cimv2", NULL, NULL)
            iWbemLevel1Login.RemRelease()

            # Получаем класс Win32_Process
            win32Process, _ = iWbemServices.GetObject("Win32_Process")

            # Запускаем команду (в скрытом окне)
            # В Win32_Process.Create параметры: CommandLine, CurrentDirectory, ProcessStartupInformation
            # Игнорируем вывод, нам нужно только запустить процесс активации WinRM
            result = win32Process.Create(command, "C:\\", None)

            # В Impacket result является IWbemClassObject. Чтобы получить значения, нужно использовать getProperties()
            props = result.getProperties()
            return_code_prop = props.get("ReturnValue")
            return_code = (
                return_code_prop["value"]
                if isinstance(return_code_prop, dict)
                else (
                    getattr(return_code_prop, "value", -1) if return_code_prop else -1
                )
            )

            process_id_prop = props.get("ProcessId")
            process_id = (
                process_id_prop["value"]
                if isinstance(process_id_prop, dict)
                else (getattr(process_id_prop, "value", 0) if process_id_prop else 0)
            )

            if return_code != 0:
                logger.error(f"WMI Win32_Process.Create вернул ошибку {return_code}")
                raise WmiBootstrapError(
                    f"Ошибка выполнения WMI команды, код: {return_code}"
                )

            logger.info(
                f"[{self.target_ip}] Процесс WMI успешно запущен, PID: {process_id}"
            )
            return return_code

        except Exception as e:
            logger.error(f"Сбой выполнения WMI команды на {self.target_ip}: {e}")
            raise WmiBootstrapError(f"Сбой WMI: {e}")
        finally:
            dcom.disconnect()

    async def execute(self, command: str, timeout: float = 30.0) -> None:
        """
        Асинхронная обертка для выполнения команды через WMI.
        """
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._sync_execute, command), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"Таймаут WMI команды ({timeout} сек) на {self.target_ip}")
            raise WmiBootstrapError(
                f"Таймаут WMI команды ({timeout} сек) на {self.target_ip}"
            )

    async def _wait_for_port(self, port: int, timeout: float = 15.0) -> bool:
        """
        Асинхронно ожидает доступности TCP-порта на целевом ПК.
        Возвращает True, если порт стал доступен, иначе False.
        """
        import time

        start_time = time.time()
        logger.debug(f"[{self.target_ip}] Ожидание доступности порта {port}...")
        while time.time() - start_time < timeout:
            try:
                # Пытаемся открыть TCP-соединение
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.target_ip, port), timeout=3.0
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                logger.debug(f"[{self.target_ip}] Порт {port} доступен.")
                return True
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(1.0)
        logger.warning(f"[{self.target_ip}] Превышен таймаут ожидания порта {port}.")
        return False

    def _read_bootstrap_log_sync(
        self, retries: int = 5, retry_delay: float = 1.5
    ) -> str | None:
        """
        Синхронно читает wmi_bootstrap.log с удаленного ПК через SMB.

        Файл может быть заблокирован активным Start-Transcript (0xc0000043),
        если PowerShell-процесс ещё не завершился. Выполняем ретраи с паузой.
        """
        import time
        from smbclient import register_session, open_file

        unc_path = f"\\\\{self.target_ip}\\C$\\Windows\\Temp\\wmi_bootstrap.log"
        logger.debug(
            f"[{self.target_ip}] Попытка прочитать лог {unc_path} через SMB..."
        )

        from worker_services.credentials import format_smb_username
        formatted_user = format_smb_username(self.target_ip, self.domain, self.username)
        # Регистрируем SMB-сессию один раз перед ретраями
        try:
            register_session(
                self.target_ip, username=formatted_user, password=self.password
            )
        except Exception as e:
            logger.debug(
                f"SMB: регистрация сессии для {self.target_ip} не удалась "
                f"(возможно, уже зарегистрирована): {e}"
            )

        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                with open_file(unc_path, mode="rb") as f:
                    content_bytes = f.read()

                # PowerShell Transcript пишет UTF-16 LE с BOM, либо UTF-8
                for enc in ("utf-8-sig", "utf-16", "utf-8"):
                    try:
                        return content_bytes.decode(enc)
                    except (UnicodeDecodeError, Exception):
                        continue
                return content_bytes.decode("utf-8", errors="replace")

            except Exception as e:
                last_error = e
                # 0xc0000043 = STATUS_SHARING_VIOLATION — файл заблокирован Transcript-ом
                if "0xc0000043" in str(e) and attempt < retries:
                    logger.debug(
                        f"[{self.target_ip}] Лог заблокирован (попытка {attempt}/{retries}), "
                        f"повтор через {retry_delay}с..."
                    )
                    time.sleep(retry_delay)
                    continue
                break

        logger.warning(
            f"[{self.target_ip}] Не удалось прочитать удаленный лог {unc_path}: {last_error}"
        )
        return None

    async def enable_winrm(self, timeout: float = 40.0) -> None:
        """
        Запускает команду включения WinRM на удаленном хосте.

        Принцип минимального воздействия:
        - НЕ меняем StartupType (не превращаем временный сеанс в постоянный автозапуск).
        - НЕ создаём новые правила брандмауэра — активируем встроенное правило Windows
          'WINRM-HTTP-In-TCP*', которое уже присутствует в системе.
        - НЕ трогаем LocalAccountTokenFilterPolicy — нужен только для локальных учёток,
          мы работаем через доменную учётную запись.
        """
        ps_script = (
            "$ErrorActionPreference = 'Continue'; "
            "Start-Transcript -Path 'C:\\Windows\\Temp\\wmi_bootstrap.log' -Force; "
            # Сначала проверяем службу через Get-Service (это не обращается к диску WSMan:\ и не вызывает автозапуск с запросом подтверждения)
            "$service = Get-Service -Name WinRM -ErrorAction SilentlyContinue; "
            "if (-not $service) { "
            "  Write-Output 'WinRM service not found.'; "
            "  Stop-Transcript; exit 1; "
            "} "
            # Гарантируем запуск службы до обращения к WSMan:\
            "if ($service.Status -ne 'Running') { "
            "  Start-Service -Name WinRM; "
            "} "
            # Теперь служба запущена, применяем настройки (они идемпотентны)
            "Set-Item WSMan:\\localhost\\Service\\Auth\\Basic -Value $true -Force; "
            "Set-Item WSMan:\\localhost\\Service\\AllowUnencrypted -Value $true -Force; "
            # Слушатель создаём только если его нет совсем (обычно уже есть)
            "if (-not (Get-ChildItem WSMan:\\localhost\\Listener -ErrorAction SilentlyContinue)) { "
            "  New-Item -Path WSMan:\\localhost\\Listener -Address * -Transport HTTP -Force; "
            "} "
            # Обязательно активируем встроенное правило Windows каждый раз, так как оно могло быть отключено в disable_winrm
            "Enable-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue; "
            "Write-Output 'WinRM setup complete.'; "
            "Stop-Transcript"
        )
        # Кодируем скрипт в Base64 для передачи через powershell -EncodedCommand
        encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("utf-8")
        cmd = f"powershell.exe -ExecutionPolicy Bypass -NoProfile -NonInteractive -EncodedCommand {encoded}"

        logger.warning(
            "[SECURITY] [%s] WinRM настраивается с AllowUnencrypted=true и Basic Auth. "
            "Допустимо только в изолированном VLAN!",
            self.target_ip,
        )
        logger.info(f"[{self.target_ip}] Инициализация включения WinRM через WMI...")
        # Таймаут передаётся напрямую в execute(), который сам оборачивает вызов в asyncio.wait_for.
        # Двойная обёртка wait_for(wait_for(...)) намеренно исключена.
        try:
            await self.execute(cmd, timeout=timeout)
        except WmiBootstrapError:
            logger.error(
                f"[{self.target_ip}] Таймаут выполнения операции включения WinRM"
            )
            raise

        # Динамическое ожидание порта 5985 вместо слепого sleep(5). Увеличено до 120 секунд для медленных ПК.
        port_opened = await self._wait_for_port(5985, timeout=120.0)
        if not port_opened:
            log_content = await asyncio.to_thread(self._read_bootstrap_log_sync)
            if log_content:
                logger.error(
                    f"[{self.target_ip}] Не удалось запустить WinRM. Содержимое wmi_bootstrap.log:\n{log_content}"
                )
            else:
                logger.error(
                    f"[{self.target_ip}] Не удалось запустить WinRM, лог wmi_bootstrap.log пуст или недоступен."
                )
            raise WmiBootstrapError(
                f"Не удалось дождаться открытия порта WinRM (5985) на {self.target_ip}"
            )

        # Небольшая пауза на стабилизацию после открытия порта
        await asyncio.sleep(1.0)
        logger.info(f"[{self.target_ip}] Ожидание запуска WinRM завершено.")

    async def disable_winrm(self, timeout: float = 30.0) -> None:
        """
        Отключает WinRM для возврата системы в безопасное состояние.

        Симметрично enable_winrm:
        - НЕ меняем StartupType обратно (мы его не меняли при включении).
        - Деактивируем встроенное правило брандмауэра 'WINRM-HTTP-In-TCP*'
          (которое активировали при включении).
        - Останавливаем службу.
        """
        ps_script = (
            # Останавливаем службу
            "Stop-Service -Name WinRM -ErrorAction SilentlyContinue; "
            # Деактивируем встроенное правило (симметрично Enable-NetFirewallRule в enable_winrm)
            "Disable-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue;"
        )
        encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("utf-8")
        cmd = f"powershell.exe -ExecutionPolicy Bypass -NoProfile -NonInteractive -EncodedCommand {encoded}"

        logger.info(f"[{self.target_ip}] Инициализация отключения WinRM через WMI...")
        # Таймаут передаётся напрямую в execute(), двойная обёртка wait_for исключена.
        try:
            await self.execute(cmd, timeout=timeout)
        except WmiBootstrapError:
            logger.error(
                f"[{self.target_ip}] Таймаут выполнения операции отключения WinRM"
            )
            raise
        logger.info(f"[{self.target_ip}] Команда отключения отправлена.")
