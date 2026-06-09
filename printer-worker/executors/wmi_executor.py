import asyncio
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
    def __init__(self, target_ip: str, username: str, password: str, domain: str = ''):
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
                '', 
                '', 
                None, 
                oxidResolver=True
            )
        except Exception as e:
            logger.error(f"Ошибка подключения DCOM к {self.target_ip}: {e}")
            raise WmiBootstrapError(f"Ошибка DCOM: {e}")

        try:
            # Получаем интерфейс IWbemLevel1Login
            iInterface = dcom.CoCreateInstanceEx(wmi.CLSID_WbemLevel1Login, wmi.IID_IWbemLevel1Login)
            iWbemLevel1Login = wmi.IWbemLevel1Login(iInterface)
            
            # Логинимся в пространство имен root\cimv2
            iWbemServices = iWbemLevel1Login.NTLMLogin('//./root/cimv2', NULL, NULL)
            iWbemLevel1Login.RemRelease()
            
            # Получаем класс Win32_Process
            win32Process, _ = iWbemServices.GetObject('Win32_Process')
            
            # Запускаем команду (в скрытом окне)
            # В Win32_Process.Create параметры: CommandLine, CurrentDirectory, ProcessStartupInformation
            # Игнорируем вывод, нам нужно только запустить процесс активации WinRM
            result = win32Process.Create(command, 'C:\\', None)
            
            return_code = result.get('ReturnValue', -1)
            process_id = result.get('ProcessId', 0)
            
            if return_code != 0:
                logger.error(f"WMI Win32_Process.Create вернул ошибку {return_code}")
                raise WmiBootstrapError(f"Ошибка выполнения WMI команды, код: {return_code}")
                
            logger.debug(f"Процесс WMI успешно запущен, PID: {process_id}")
            return return_code
            
        except Exception as e:
            logger.error(f"Сбой выполнения WMI команды на {self.target_ip}: {e}")
            raise WmiBootstrapError(f"Сбой WMI: {e}")
        finally:
            dcom.disconnect()

    async def execute(self, command: str) -> None:
        """
        Асинхронная обертка для выполнения команды через WMI.
        """
        await asyncio.to_thread(self._sync_execute, command)

    async def enable_winrm(self) -> None:
        """
        Запускает команду включения WinRM на удаленном хосте.
        Эквивалент: winrm quickconfig -q
        """
        # Запускаем PowerShell в фоне, который сконфигурирует WinRM, настроит Basic Auth и нешифрованный трафик
        # (Требуется для pywinrm с Basic аутентификацией, если не используется NTLM/Kerberos)
        # Для безопасности лучше использовать Kerberos/NTLM, но pywinrm из коробки часто требует AllowUnencrypted
        # В энтерпрайз среде лучше адаптировать этот скрипт под нужды безопасности.
        ps_script = (
            "Enable-PSRemoting -Force; "
            "Set-Item WSMan:\\localhost\\Service\\Auth\\Basic -Value $true; "
            "Set-Item WSMan:\\localhost\\Service\\AllowUnencrypted -Value $true; "
            "Restart-Service WinRM"
        )
        # Кодируем скрипт в Base64 для передачи через powershell -EncodedCommand
        import base64
        encoded = base64.b64encode(ps_script.encode('utf-16le')).decode('utf-8')
        cmd = f"powershell.exe -ExecutionPolicy Bypass -NoProfile -NonInteractive -EncodedCommand {encoded}"
        
        logger.info(f"[{self.target_ip}] Инициализация включения WinRM через WMI...")
        await self.execute(cmd)
        
        # Даем службе время на запуск
        await asyncio.sleep(5)
        logger.info(f"[{self.target_ip}] Ожидание запуска WinRM завершено.")

    async def disable_winrm(self) -> None:
        """
        Отключает WinRM для возврата системы в безопасное состояние.
        """
        ps_script = "Stop-Service WinRM; Set-Service WinRM -StartupType Manual"
        import base64
        encoded = base64.b64encode(ps_script.encode('utf-16le')).decode('utf-8')
        cmd = f"powershell.exe -ExecutionPolicy Bypass -NoProfile -NonInteractive -EncodedCommand {encoded}"
        
        logger.info(f"[{self.target_ip}] Инициализация отключения WinRM через WMI...")
        await self.execute(cmd)
        logger.info(f"[{self.target_ip}] Команда отключения отправлена.")
