import asyncio
import base64
import logging
from impacket.dcerpc.v5 import transport, scmr
from impacket.smbconnection import SMBConnection
import time

logger = logging.getLogger(__name__)

class SmbBootstrapError(Exception):
    pass

class SMBBootstrapExecutor:
    """
    Класс для выполнения команд на удаленном сервере через создание временной службы 
    по протоколу SMB (SMBExec / Service Control Manager).
    Используется как запасной вариант (Fallback) для активации/деактивации WinRM,
    когда недоступны порты DCOM/WMI, но открыт порт 445 (SMB).
    """

    def __init__(self, target_ip: str, username: str, password: str, domain: str = ""):
        self.target_ip = target_ip
        self.username = username
        self.password = password
        self.domain = domain

    def _sync_execute_service(self, command: str) -> None:
        """
        Синхронное создание и запуск временной службы через SMB (порт 445).
        """
        logger.debug(f"[{self.target_ip}] Подключение к SMB для выполнения команды через SCM...")

        smb = None
        rpctransport = None
        try:
            # Устанавливаем SMB-соединение явно на порт 445, игнорируя NetBIOS
            smb = SMBConnection(self.target_ip, self.target_ip, sess_port=445)
            smb.login(self.username, self.password, self.domain)
            
            # Подготавливаем RPC транспорт поверх существующего SMB-подключения
            rpctransport = transport.SMBTransport(self.target_ip, 445, r'\svcctl', smb_connection=smb)
            dce = rpctransport.get_dce_rpc()
            dce.connect()
            dce.bind(scmr.MSRPC_UUID_SCMR)
            
            # Открываем Service Control Manager
            ans = scmr.hROpenSCManagerW(dce)
            scManagerHandle = ans['lpScHandle']
            
            serviceName = 'IntraLink_WinRM_Bootstrap'
            # Команда запускается через cmd.exe
            binPath = r'%COMSPEC% /Q /c ' + command

            serviceHandle = None
            try:
                logger.debug(f"[{self.target_ip}] Создание временной службы {serviceName}...")
                resp = scmr.hRCreateServiceW(dce, scManagerHandle, serviceName, serviceName,
                                             lpBinaryPathName=binPath)
                serviceHandle = resp['lpServiceHandle']
            except Exception as e:
                # Если служба уже существует от предыдущего прерванного запуска
                if "ERROR_DUPLICATE_SERVICE_NAME" in str(e) or "ERROR_SERVICE_EXISTS" in str(e):
                    logger.warning(f"[{self.target_ip}] Служба {serviceName} уже существует, перенастраиваем...")
                    resp = scmr.hROpenServiceW(dce, scManagerHandle, serviceName)
                    serviceHandle = resp['lpServiceHandle']
                    scmr.hRChangeServiceConfigW(dce, serviceHandle, lpBinaryPathName=binPath)
                else:
                    raise SmbBootstrapError(f"Не удалось создать службу SCM: {e}")
                    
            logger.info(f"[{self.target_ip}] Запуск временной службы (SMBExec)...")
            try:
                scmr.hRStartServiceW(dce, serviceHandle)
            except Exception as e:
                # Обычно запуск cmd.exe быстро завершается, и SCM возвращает ошибку таймаута старта службы 
                # (ERROR_SERVICE_REQUEST_TIMEOUT) или служба не отвечает на контрольные сигналы. 
                # Это ожидаемо, так как мы не писали полноценный бинарник сервиса.
                logger.debug(f"[{self.target_ip}] Результат запуска службы (обычно игнорируемая ошибка): {e}")
                
            logger.debug(f"[{self.target_ip}] Удаление временной службы...")
            scmr.hRDeleteService(dce, serviceHandle)
            scmr.hRCloseServiceHandle(dce, serviceHandle)
            scmr.hRCloseServiceHandle(dce, scManagerHandle)

            dce.disconnect()
            logger.info(f"[{self.target_ip}] Команда через SMBExec успешно передана.")
        except Exception as e:
            logger.error(f"Сбой выполнения SMBExec команды на {self.target_ip}: {e}")
            raise SmbBootstrapError(f"Сбой SMBExec: {e}")
        finally:
            if smb is not None:
                smb.logoff()

    async def execute(self, command: str, timeout: float = 30.0) -> None:
        """
        Асинхронная обертка для выполнения команды через SMB.
        """
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._sync_execute_service, command), timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.error(f"Таймаут SMBExec команды ({timeout} сек) на {self.target_ip}")
            raise SmbBootstrapError(
                f"Таймаут SMBExec команды ({timeout} сек) на {self.target_ip}"
            )

    async def _wait_for_port(self, port: int, timeout: float = 15.0) -> bool:
        start_time = time.time()
        logger.debug(f"[{self.target_ip}] Ожидание доступности порта {port}...")
        while time.time() - start_time < timeout:
            try:
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

    async def enable_winrm(self, timeout: float = 40.0) -> None:
        ps_script = (
            "$ErrorActionPreference = 'Continue'; "
            # Стартуем службу WinRM, если не запущена
            "$service = Get-Service -Name WinRM -ErrorAction SilentlyContinue; "
            "if (-not $service) { exit 1; } "
            "if ($service.Status -ne 'Running') { Start-Service -Name WinRM; } "
            # Применяем настройки
            "Set-Item WSMan:\\localhost\\Service\\Auth\\Basic -Value $true -Force; "
            "Set-Item WSMan:\\localhost\\Service\\AllowUnencrypted -Value $true -Force; "
            "if (-not (Get-ChildItem WSMan:\\localhost\\Listener -ErrorAction SilentlyContinue)) { "
            "  New-Item -Path WSMan:\\localhost\\Listener -Address * -Transport HTTP -Force; "
            "} "
            "Enable-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue;"
        )
        encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("utf-8")
        cmd = f"powershell.exe -ExecutionPolicy Bypass -NoProfile -NonInteractive -EncodedCommand {encoded}"

        logger.warning(
            "[SECURITY] [%s] WinRM настраивается через SMB Fallback (AllowUnencrypted=true).",
            self.target_ip,
        )
        
        try:
            await self.execute(cmd, timeout=timeout)
        except SmbBootstrapError:
            logger.error(f"[{self.target_ip}] Таймаут выполнения операции включения WinRM по SMB")
            raise

        port_opened = await self._wait_for_port(5985, timeout=120.0)
        if not port_opened:
            raise SmbBootstrapError(
                f"Не удалось дождаться открытия порта WinRM (5985) на {self.target_ip} после SMBExec"
            )
        await asyncio.sleep(1.0)
        logger.info(f"[{self.target_ip}] Ожидание запуска WinRM (SMBExec) завершено.")

    async def disable_winrm(self, timeout: float = 30.0) -> None:
        ps_script = (
            "Stop-Service -Name WinRM -ErrorAction SilentlyContinue; "
            "Disable-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue;"
        )
        encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("utf-8")
        cmd = f"powershell.exe -ExecutionPolicy Bypass -NoProfile -NonInteractive -EncodedCommand {encoded}"

        logger.info(f"[{self.target_ip}] Инициализация отключения WinRM через SMB Fallback...")
        try:
            await self.execute(cmd, timeout=timeout)
        except SmbBootstrapError:
            logger.error(f"[{self.target_ip}] Таймаут выполнения операции отключения WinRM (SMBExec)")
            raise
        logger.info(f"[{self.target_ip}] Команда отключения (SMBExec) отправлена.")
